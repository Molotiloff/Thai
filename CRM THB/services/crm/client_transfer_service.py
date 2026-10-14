from __future__ import annotations

from typing import Protocol

from domain import DealEventPayload
from domain.finance.models import InvalidPosting, decimal, exact
from services.clients.ports import ClientTransferPostingPort, ClientTransferReadPort
from services.exchange.ledger import TradingReadPort
from services.finance.ports import FinanceUowFactory
from services.finance.posting import ReversalService
from services.metadata_commands import MetadataCommandService

from .client_transfer_models import (
    ClientTransferAdjustmentCommand,
    ClientTransferAdjustmentResult,
    ClientTransferCommand,
    ClientTransferResult,
)
from .deal_events import DealEvent, DealEventBus

__all__ = [
    "ClientTransferAdjustmentCommand",
    "ClientTransferAdjustmentResult",
    "ClientTransferCommand",
    "ClientTransferResult",
    "ClientTransferService",
]


class ClientTransferRepositoryPort(Protocol):
    async def find_recipients(self, name: str) -> list[dict[str, int | str]]: ...

    async def transfer(self, command: ClientTransferCommand) -> ClientTransferResult: ...

    async def reject(self, command: ClientTransferCommand) -> bool: ...

    async def adjust(
        self, command: ClientTransferAdjustmentCommand
    ) -> ClientTransferAdjustmentResult: ...


class ClientTransferService:
    def __init__(
        self, repository: ClientTransferRepositoryPort | None = None, event_bus: DealEventBus | None = None,
        *, uow_factory: FinanceUowFactory | None = None, queries: ClientTransferReadPort | None = None,
        transfers: ClientTransferPostingPort | None = None, reversals: ReversalService | None = None,
        trading_queries: TradingReadPort | None = None
    ) -> None:
        if uow_factory is not None and any(dependency is None for dependency in
                (queries,transfers,reversals,trading_queries)):
            raise ValueError('Ledger transfers require UoW, read/posting ports and reversal engine')
        self._repository = repository
        self._event_bus = event_bus
        self._commands = MetadataCommandService(uow_factory) if uow_factory else None
        self._queries, self._transfers, self._reversals = queries, transfers, reversals
        self._trading_queries = trading_queries

    async def find_recipients(self, name: str) -> list[dict[str, int | str]]:
        return await self._repository.find_recipients(name)

    async def transfer(self, command: ClientTransferCommand) -> ClientTransferResult:
        result = await self._repository.transfer(command)
        if not result.repeated:
            await self._event_bus.publish(
                DealEvent(
                    type="deal.created",
                    deal_id=result.deal_id,
                    payload=DealEventPayload({"source_kind": "client_transfer"}),
                )
            )
        return result

    async def reject(self, command: ClientTransferCommand) -> bool:
        return await self._repository.reject(command)

    async def adjust(
        self, command: ClientTransferAdjustmentCommand
    ) -> ClientTransferAdjustmentResult:
        result = await self._repository.adjust(command)
        if not result.repeated:
            await self._event_bus.publish(DealEvent(
                type="deal.updated", deal_id=result.deal_id,
                payload=DealEventPayload({"source_kind": "client_transfer"}),
            ))
        return result


    @staticmethod
    def request_key(chat_id: int, message_id: int) -> str:
        return f'tg:{chat_id}:{message_id}:client-transfer'

    async def prepare_accounting(self, *, key, actor_id, source_client_id, source_chat_id,
                                 quantity, instrument, recipient_name, effective_at):
        """Persist the terms and candidate list; no balance changes before confirmation."""
        if await self._trading_queries.client_by_chat(source_chat_id)!=source_client_id:
            raise InvalidPosting('Перевод доступен только из действующего чата отправителя')
        accounts = await self._queries.wallet_accounts(source_client_id,instrument)
        quantity = exact(decimal(quantity),accounts['precision'])
        if quantity<=0:
            raise InvalidPosting('Сумма перевода должна быть больше нуля')
        recipients = [r for r in await self._queries.transfer_recipients(recipient_name)
                      if r['id']!=source_client_id]
        if not recipients:
            raise InvalidPosting('Клиентский чат получателя не найден или совпадает с отправителем')
        payload = {'source_client':source_client_id,'source_chat':source_chat_id,
                   'quantity':quantity,'instrument':instrument,'recipient_name':recipient_name}
        async def action(work):
            await work.clients.lock_client(source_client_id)
            return {**payload,'quantity':str(quantity),'candidates':recipients}
        return await self._commands.execute(key=key,actor_id=actor_id,kind='client_transfer_request',
            payload=payload,action=action,permission='finance.post')

    async def resolve_accounting(self, *, key, actor_id, recipient_id, confirmed,
                                 business_date, effective_at):
        request = await self._queries.transfer_request(key)
        if recipient_id not in [r['id'] for r in request['candidates']]:
            raise InvalidPosting('Получатель не принадлежит этой заявке')
        async def action(work):
            if not confirmed:
                return {'cancelled':True,'operation_id':None}
            instrument = request['instrument']
            quote = next((q for q in await self._trading_queries.rates(at=effective_at)
                          if q['instrument']==instrument),None)
            if instrument!='USDT' and (quote is None or quote['quoted_rate'] is None):
                raise InvalidPosting('Не задан курс себестоимости валюты относительно USDT: /курс')
            operation = await self._transfers.transfer(key=key+':post',actor_id=actor_id,
                source_client_id=request['source_client'],target_client_id=recipient_id,
                instrument=instrument,quantity=request['quantity'],business_date=business_date,
                effective_at=effective_at,quoted_rate=quote['quoted_rate'] if quote else None,work=work)
            return {'cancelled':False,'operation_id':operation.id}
        # One resolution key for ALL candidates: stale buttons cannot create another transfer.
        return await self._commands.execute(key=key+':resolve',actor_id=actor_id,
            kind='client_transfer_resolve',permission='finance.post',
            payload={'request':key,'recipient':recipient_id,'confirmed':confirmed},action=action)

    async def adjust_accounting(self, *, key, actor_id, operation_id, new_amount,
                                business_date, effective_at, source_chat_id=None,
                                profit_revision_id=None):
        context = await self._queries.transfer_context(operation_id)
        root_id = context['root_id']
        if source_chat_id is not None and source_chat_id!=context['source']['chat_id']:
            raise InvalidPosting('Изменение перевода доступно только в чате отправителя')
        amount = None if new_amount is None else decimal(new_amount)
        if amount is not None and amount<=0:
            raise InvalidPosting('Сумма перевода должна быть больше нуля')
        async def action(work):
            await work.journal.lock_posting_period(profit_revision_id=profit_revision_id)
            await work.clients.lock_transfer(root_id)
            current = await work.clients.transfer_context(root_id)
            if current['cancelled']:
                raise InvalidPosting('Перевод уже отменён')
            details = current['details']
            if amount is not None:
                accounts = await self._queries.wallet_accounts(details['source_client'],details['instrument'])
                exact(amount,accounts['precision'])
                if amount==decimal(details['quantity']):
                    return {'root_id':root_id,'operation_id':current['operation_id'],'cancelled':False}
            for client_id in sorted((details['source_client'],details['target_client'])):
                await work.clients.lock_client(client_id)
            revision = {'profit_revision_id':profit_revision_id} if profit_revision_id else {}
            await self._reversals.reverse(original_id=current['operation_id'],key=key+':reverse',
                actor_id=actor_id,business_date=business_date,effective_at=effective_at,
                reason='Отмена перевода' if amount is None else 'Исправление суммы перевода',
                details=revision,work=work)
            replacement = None
            if amount is not None:
                replacement = await self._transfers.transfer(key=key+':replacement',actor_id=actor_id,
                    source_client_id=details['source_client'],target_client_id=details['target_client'],
                    instrument=details['instrument'],quantity=amount,quoted_rate=details['quoted_rate'],
                    business_date=business_date,effective_at=effective_at,comment=details['comment'],
                    profit_revision_id=profit_revision_id,transfer_root_id=root_id,work=work)
            return {'root_id':root_id,'operation_id':replacement.id if replacement else None,'cancelled':amount is None}
        return await self._commands.execute(key=key,actor_id=actor_id,kind='client_transfer_adjust',
            permission='finance.reverse',payload={'root_id':root_id,'amount':amount,
                'business_date':business_date,'effective_at':effective_at,'profit_revision_id':profit_revision_id},action=action)
