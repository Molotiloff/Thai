from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import cast

from domain import DealStatus, DomainStateError, DomainValidationError
from domain.finance.models import FxQuote, InvalidPosting, decimal
from services.crm.deal_service import DealStatusCommand
from services.exchange.ledger import TradingReadPort, TradingUnitOfWork
from services.finance.ports import FinanceUowFactory
from services.metadata_commands import MetadataCommandService
from services.unit_of_work import UnitOfWorkFactory

from .cash_settlement_models import (
    CashExchangePostingPort,
    CashSettlementCommand,
    CashSettlementContext,
    CashSettlementResult,
)


class CashSettlementError(DomainStateError):
    pass


class CashSettlementService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory | None = None,
        *, ledger_uow_factory: FinanceUowFactory | None = None,
        trading: CashExchangePostingPort | None = None, queries: TradingReadPort | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        if ledger_uow_factory is not None:
            if trading is None or queries is None:
                raise ValueError('Cash exchange settlement requires trading and read ports')
            self._commands = MetadataCommandService(ledger_uow_factory)
            self._trading,self._queries = trading,queries
        elif unit_of_work_factory is None:
            raise ValueError('Cash settlement requires a unit of work factory')

    async def confirm_exchange(self, *, request_id, instrument, signed_quantity, quoted_rate=None,
                               profit_revision_id=None, **common):
        deal = await self._queries.detail(request_id)
        if deal['kind']!='cash_exchange':
            raise InvalidPosting('Это не двухвалютная кассовая заявка')
        signed = decimal(signed_quantity)
        direction = 'receive' if signed>0 else 'pay'
        leg = deal['attributes'][direction]
        if instrument!=leg['instrument'] or abs(signed)!=decimal(leg['quantity']) or signed==0:
            raise InvalidPosting('Валюта, сумма или знак не совпадают с заявкой')
        if quoted_rate is not None:
            accounts = await self._queries.accounts(deal['client_id'],deal['attributes']['receive']['instrument'],
                deal['attributes']['pay']['instrument'],'cash')
            currency = accounts[direction]['currency']
            FxQuote(currency,decimal(quoted_rate),'units_per_usdt' if currency=='THB' else 'usdt_per_unit',instrument)
        payload = {'deal_id':request_id,'instrument':instrument,'direction':direction,'quantity':abs(signed),
            'quoted_rate':None if quoted_rate is None else decimal(quoted_rate),
            'effective_at':common['effective_at'],'business_date':common['business_date'],'profit_revision_id':profit_revision_id}
        async def action(work):
            uow = cast(TradingUnitOfWork,work)
            await uow.journal.lock_posting_period(profit_revision_id=profit_revision_id)
            await uow.clients.lock_client(deal['client_id'])
            current = await uow.trades.lock(request_id)
            if current['status']=='posted':
                return {'deal_id':request_id,'operation_id':current['operation_id'],'status':'posted'}
            if current['status']!='ready':
                raise InvalidPosting('Сначала отметьте заявку готовой к расчёту')
            await uow.trades.confirm_cash_exchange(request_id,direction,instrument,abs(signed),quoted_rate,
                common['actor_id'],common['effective_at'])
            confirmations = await uow.trades.cash_confirmations(request_id)
            operation_id = None
            if len(confirmations)==2:
                posted = await self._trading.post(**{**common,'key':f'cash-exchange:{request_id}'},deal_id=request_id,
                    work=uow,profit_revision_id=profit_revision_id)
                operation_id = posted.id
            await uow.trades.cash_request_changed(request_id)
            return {'deal_id':request_id,'operation_id':operation_id,'status':'posted' if operation_id else 'ready'}
        return await self._commands.execute(key=common['key']+':confirm',actor_id=common['actor_id'],
            kind='cash_request_confirm',payload=payload,action=action,permission='finance.post')

    async def mark_ready(
        self,
        *,
        request_id: str,
        actor_tg_user_id: int | None,
    ) -> int:
        async with self._unit_of_work_factory() as unit_of_work:
            context = await unit_of_work.cash_settlements.get_request_for_update(
                request_id=request_id
            )
            if context is None:
                raise CashSettlementError(f"Кассовая заявка {request_id} не найдена.")
            if context.deal_status is DealStatus.READY_FOR_CASH_SETTLEMENT:
                await unit_of_work.commit()
                return context.deal_id
            if context.deal_status is not DealStatus.NEW:
                raise CashSettlementError(
                    f"Заявка {request_id} имеет статус {context.deal_status}."
                )
            changed = await unit_of_work.deals.change_status(
                context.deal_id,
                DealStatusCommand(
                    status=DealStatus.READY_FOR_CASH_SETTLEMENT,
                    expected_old_status=DealStatus.NEW,
                    actor_user_id=None,
                    payload={
                        "requestId": request_id,
                        "actorTgUserId": actor_tg_user_id,
                    },
                ),
            )
            if changed is None:
                raise CashSettlementError(f"Сделка заявки {request_id} не найдена.")
            await unit_of_work.commit()
            return context.deal_id

    async def is_settled(self, *, request_id: str) -> bool:
        async with self._unit_of_work_factory() as unit_of_work:
            return (
                await unit_of_work.cash_settlements.get_by_request(
                    request_id=request_id
                )
                is not None
            )

    async def cancel_request(
        self,
        *,
        request_id: str,
        actor_tg_user_id: int | None,
    ) -> int:
        async with self._unit_of_work_factory() as unit_of_work:
            context = await unit_of_work.cash_settlements.get_request_for_update(
                request_id=request_id
            )
            if context is None:
                raise CashSettlementError(f"Кассовая заявка {request_id} не найдена.")
            if await unit_of_work.cash_settlements.get_by_request(
                request_id=request_id
            ) is not None:
                raise CashSettlementError("Проведенную кассовую заявку нельзя отменить.")
            if context.deal_status is DealStatus.CANCELED:
                await unit_of_work.commit()
                return context.deal_id
            if context.deal_status not in {
                DealStatus.NEW,
                DealStatus.READY_FOR_CASH_SETTLEMENT,
            }:
                raise CashSettlementError(
                    f"Заявка {request_id} имеет статус {context.deal_status}."
                )
            changed = await unit_of_work.deals.change_status(
                context.deal_id,
                DealStatusCommand(
                    status=DealStatus.CANCELED,
                    expected_old_status=context.deal_status,
                    actor_user_id=None,
                    payload={
                        "requestId": request_id,
                        "actorTgUserId": actor_tg_user_id,
                    },
                ),
            )
            if changed is None:
                raise CashSettlementError(f"Сделка заявки {request_id} не найдена.")
            await unit_of_work.request_schedule.deactivate_request_schedule_entry(
                request_id
            )
            await unit_of_work.commit()
            return context.deal_id

    async def settle(self, command: CashSettlementCommand) -> CashSettlementResult:
        request_id = command.request_id.strip()
        currency = command.currency.strip().upper()
        signed_qty = Decimal(command.signed_qty)
        if not request_id:
            raise DomainValidationError("Cash settlement request id is required")
        if not signed_qty.is_finite() or signed_qty == 0:
            raise DomainValidationError("Cash settlement quantity must be non-zero")

        async with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.cash_settlements
            await repository.acquire_request_lock(request_id=request_id)
            repeated = await repository.get_by_request(request_id=request_id)
            if repeated is not None:
                self._validate_repeated(
                    repeated,
                    currency=currency,
                    signed_qty=signed_qty,
                )
                await unit_of_work.commit()
                return replace(repeated, repeated=True)

            context = await repository.get_context_for_update(
                request_id=request_id,
                city_chat_id=command.city_chat_id,
            )
            if context is None:
                raise CashSettlementError(
                    f"Заявка {request_id} не найдена в активной кассе этого города."
                )
            self._validate_command(context, currency=currency, signed_qty=signed_qty)
            actual_qty = abs(signed_qty)
            if context.request_kind == "wd":
                cash_balance = await repository.get_cash_balance_for_update(
                    client_id=context.cash_client_id, currency=currency,
                )
                if cash_balance is None:
                    raise CashSettlementError(f"В кассе нет активного счёта {currency}.")
                if actual_qty > cash_balance:
                    raise CashSettlementError(
                        f"Недостаточно {currency} в кассе: остаток {cash_balance}, "
                        f"к выдаче {actual_qty}."
                    )
            operation = (
                unit_of_work.transactions.deposit
                if context.request_kind == "dep"
                else unit_of_work.transactions.withdraw
            )
            cash_transaction_id = await operation(
                client_id=context.cash_client_id,
                currency_code=currency,
                amount=actual_qty,
                comment=f"cash settlement {request_id}",
                source="cash_settlement",
                idempotency_key=f"cash_settlement:{request_id}:cash",
            )
            client_transaction_id = None
            if context.track_client_balance:
                client_transaction_id = await operation(
                    client_id=context.client_id,
                    currency_code=currency,
                    amount=actual_qty,
                    comment=f"cash settlement {request_id}",
                    source="cash_settlement",
                    idempotency_key=f"cash_settlement:{request_id}:client",
                )

            result = await repository.insert(
                context=context,
                command=command,
                actual_qty=actual_qty,
                cash_transaction_id=cash_transaction_id,
                client_transaction_id=client_transaction_id,
                position_move_id=None,
            )
            await unit_of_work.request_schedule.deactivate_request_schedule_entry(
                request_id
            )
            changed = await unit_of_work.deals.change_status(
                context.deal_id,
                DealStatusCommand(
                    status=DealStatus.DONE,
                    expected_old_status=DealStatus.READY_FOR_CASH_SETTLEMENT,
                    actor_user_id=None,
                    payload={
                        "requestId": request_id,
                        "cashSettlementId": result.settlement_id,
                        "actualQty": str(actual_qty),
                        "currency": currency,
                        "actorTgUserId": command.actor_tg_user_id,
                        "evidence": command.evidence,
                    },
                    body_patch={
                        "cash_settlement_id": result.settlement_id,
                        "settled_qty": str(actual_qty),
                    },
                ),
            )
            if changed is None:
                raise CashSettlementError(f"Сделка заявки {request_id} не найдена.")
            await unit_of_work.commit()
            return result

    @staticmethod
    def _validate_command(
        context: CashSettlementContext,
        *,
        currency: str,
        signed_qty: Decimal,
    ) -> None:
        if context.deal_status is not DealStatus.READY_FOR_CASH_SETTLEMENT:
            raise CashSettlementError(
                "Сначала нажмите «Готово к расчету» в карточке заявки."
            )
        if currency != context.currency:
            raise CashSettlementError(
                f"В заявке {context.currency}, а команда использует {currency}."
            )
        expected_positive = context.request_kind == "dep"
        if (signed_qty > 0) != expected_positive:
            action = "положительной" if expected_positive else "отрицательной"
            raise CashSettlementError(f"Сумма для этой заявки должна быть {action}.")

    @staticmethod
    def _validate_repeated(
        result: CashSettlementResult,
        *,
        currency: str,
        signed_qty: Decimal,
    ) -> None:
        expected_positive = result.request_kind == "dep"
        if (
            result.currency != currency
            or result.actual_qty != abs(signed_qty)
            or (signed_qty > 0) != expected_positive
        ):
            raise CashSettlementError(
                "Заявка уже проведена другой кассовой командой или суммой."
            )
