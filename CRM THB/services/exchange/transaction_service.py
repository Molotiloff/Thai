from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import cast

from domain import DealStatus, DomainStateError
from domain.deal_policy import exchange_type
from domain.finance.fx_result import balance_with_fx
from domain.finance.models import (
    FxQuote,
    InvalidPosting,
    JournalLine,
    OperationCommand,
    Side,
    Valuation,
    canonical_json,
    decimal,
    exact,
    revision_details,
    rounded,
)
from domain.wallet_valuation import liability_change
from services.accounting.fulfillment_models import (
    EnqueueFulfillment,
    FulfillmentRequestKind,
)
from services.act_counter import AppliedExchangeMovement
from services.crm.deal_service import DealCreateCommand, DealService, DealStatusCommand
from services.exchange.ledger import MissingMarketQuote, TradingReadPort, TradingUnitOfWork
from services.finance.posting import CallbackDocument
from services.metadata_commands import MetadataCommandService
from services.unit_of_work import UnitOfWorkFactory

from .balance_service import ExchangeBalanceService
from .workflow_policy import tracked_exchange_currencies

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CreateExchangeTransaction:
    request_id: str
    table_request_id: str
    client_id: int
    receive_code: str
    receive_amount: Decimal
    receive_comment: str
    pay_code: str
    pay_amount: Decimal
    pay_comment: str
    rate: Decimal
    receive_is_deposit: bool
    pay_is_withdraw: bool
    receive_idempotency_key: str
    pay_idempotency_key: str
    tracked_currency_codes: frozenset[str] | None = None
    deal_command: DealCreateCommand | None = None


@dataclass(frozen=True, slots=True)
class EditExchangeTransaction:
    request_id: str
    table_request_id: str
    client_id: int
    old_card_text: str
    receive_code: str
    receive_amount: Decimal
    receive_precision: int
    pay_code: str
    pay_amount: Decimal
    pay_precision: int
    rate: Decimal
    chat_id: int
    card_message_id: int
    command_message_id: int
    receive_is_deposit: bool
    pay_is_withdraw: bool
    tracked_currency_codes: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class CancelExchangeTransaction:
    request_id: str
    table_request_id: str
    client_id: int
    receive_code: str
    receive_amount: Decimal
    pay_code: str
    pay_amount: Decimal
    chat_id: int
    card_message_id: int
    receive_is_deposit: bool
    pay_is_withdraw: bool
    tracked_currency_codes: frozenset[str] | None = None
    request_chat_id: int | None = None


@dataclass(frozen=True, slots=True)
class CreateExchangeTransactionResult:
    movements: tuple[AppliedExchangeMovement, ...]


@dataclass(frozen=True, slots=True)
class EditExchangeTransactionResult:
    movements: tuple[AppliedExchangeMovement, ...]


@dataclass(frozen=True, slots=True)
class CancelExchangeTransactionResult:
    receive_operation_sign: str | None
    pay_operation_sign: str | None
    client_id: int


class ExchangeTransactionService:
    """Owns atomic ledger and exchange source-link mutations."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        balance_service: ExchangeBalanceService,
        deal_service: DealService | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._balance_service = balance_service
        self._deal_service = deal_service

    async def create(self, command: CreateExchangeTransaction) -> CreateExchangeTransactionResult:
        async with self._unit_of_work_factory() as unit_of_work:
            balance_result = await self._balance_service.apply_create(
                client_id=command.client_id,
                recv_code=command.receive_code,
                recv_amount=command.receive_amount,
                recv_comment=command.receive_comment,
                pay_code=command.pay_code,
                pay_amount=command.pay_amount,
                pay_comment=command.pay_comment,
                recv_is_deposit=command.receive_is_deposit,
                pay_is_withdraw=command.pay_is_withdraw,
                idem_recv=command.receive_idempotency_key,
                idem_pay=command.pay_idempotency_key,
                tracked_currency_codes=_mutable_codes(command.tracked_currency_codes),
                unit_of_work=unit_of_work,
            )
            await unit_of_work.exchange_requests.upsert_exchange_request_link(
                client_req_id=command.request_id,
                table_req_id=command.table_request_id,
                table_in_cur=command.receive_code,
                table_out_cur=command.pay_code,
                table_in_amount=command.receive_amount,
                table_out_amount=command.pay_amount,
                table_rate=command.rate,
                status="active",
            )
            if command.deal_command is not None:
                deal, _ = await unit_of_work.deals.create_deal_idempotent(
                    command.deal_command
                )
                if str(command.deal_command.deal_type) == "sale" and (
                    command.pay_code.upper() == "USDT"
                ):
                    await unit_of_work.fulfillment_queue.enqueue(
                        EnqueueFulfillment(
                            deal_id=deal.id,
                            request_kind=FulfillmentRequestKind.SALE,
                            qty=command.pay_amount,
                            actor_user_id=command.deal_command.actor_user_id,
                        )
                    )
            await unit_of_work.commit()
        return CreateExchangeTransactionResult(tuple(balance_result.movements))

    async def edit(self, command: EditExchangeTransaction) -> EditExchangeTransactionResult:
        async with self._unit_of_work_factory() as unit_of_work:
            deal = await unit_of_work.deals.get_by_exchange_request_id(command.request_id)
            if deal is not None and (deal.body.get("referrer_spread_payment") or {}).get("status") == "paid":
                raise DomainStateError("Нельзя менять заявку после выплаты спреда КТ; отмените сделку")
            movements = await self._balance_service.apply_edit_delta(
                client_id=command.client_id,
                old_request_text=command.old_card_text,
                recv_code_new=command.receive_code,
                pay_code_new=command.pay_code,
                recv_amount_new=command.receive_amount,
                pay_amount_new=command.pay_amount,
                recv_prec=command.receive_precision,
                pay_prec=command.pay_precision,
                chat_id=command.chat_id,
                target_bot_msg_id=command.card_message_id,
                cmd_msg_id=command.command_message_id,
                recv_is_deposit=command.receive_is_deposit,
                pay_is_withdraw=command.pay_is_withdraw,
                tracked_currency_codes=_mutable_codes(command.tracked_currency_codes),
                unit_of_work=unit_of_work,
            )
            await unit_of_work.exchange_requests.upsert_exchange_request_link(
                client_req_id=command.request_id,
                table_req_id=command.table_request_id,
                table_in_cur=command.receive_code,
                table_out_cur=command.pay_code,
                table_in_amount=command.receive_amount,
                table_out_amount=command.pay_amount,
                table_rate=command.rate,
            )
            await unit_of_work.commit()
        return EditExchangeTransactionResult(tuple(movements))

    async def cancel(self, command: CancelExchangeTransaction) -> CancelExchangeTransactionResult:
        updated_deal = None
        async with self._unit_of_work_factory() as unit_of_work:
            deal = await unit_of_work.deals.get_by_exchange_request_id(command.request_id)
            if deal is not None and deal.status in {DealStatus.DONE, DealStatus.CANCELED}:
                raise DomainStateError(f"Сделка уже имеет статус {deal.status}")
            if not await unit_of_work.exchange_requests.claim_exchange_request_cancellation(
                client_req_id=command.request_id
            ):
                raise DomainStateError("Заявка уже отменена или связь с ней не найдена")
            client_id = int(deal.client_id) if deal is not None and deal.client_id else command.client_id
            receive_is_deposit = command.receive_is_deposit
            pay_is_withdraw = command.pay_is_withdraw
            tracked_codes = command.tracked_currency_codes
            if deal is not None:
                body = deal.body.to_dict()
                if isinstance(body.get("recv_is_deposit"), bool):
                    receive_is_deposit = body["recv_is_deposit"]
                    pay_is_withdraw = body["pay_is_withdraw"]
                elif str(deal.source) == "crm":
                    receive_is_deposit = pay_is_withdraw = True
                else:
                    try:
                        origin_chat_id = int(str(deal.source_ref).split(":", 1)[0])
                    except (TypeError, ValueError):
                        origin_chat_id = command.chat_id
                    origin_is_request = (
                        command.request_chat_id is not None
                        and origin_chat_id == command.request_chat_id
                    )
                    receive_is_deposit = pay_is_withdraw = origin_is_request
                if str(deal.source) != "crm":
                    try:
                        origin_chat_id = int(str(deal.source_ref).split(":", 1)[0])
                        tracked_codes = tracked_exchange_currencies(
                            origin_chat_id, command.request_chat_id
                        )
                    except (TypeError, ValueError):
                        pass
                else:
                    tracked_codes = None
            receive_sign, pay_sign = await self._balance_service.apply_cancel(
                client_id=client_id,
                chat_id=command.chat_id,
                message_id=command.card_message_id,
                req_id=command.request_id,
                recv_code=command.receive_code,
                recv_amount=command.receive_amount,
                pay_code=command.pay_code,
                pay_amount=command.pay_amount,
                recv_is_deposit=receive_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
                tracked_currency_codes=_mutable_codes(tracked_codes),
                unit_of_work=unit_of_work,
            )
            if deal is not None:
                changed = await unit_of_work.deals.change_status(
                    deal.id,
                    DealStatusCommand(
                        status=DealStatus.CANCELED,
                        expected_old_status=deal.status,
                        actor_user_id=None,
                        payload={"reason": "exchange_cancelled_in_telegram"},
                    ),
                )
                if changed is None:
                    raise DomainStateError(f"CRM deal for request {command.request_id} disappeared")
                updated_deal = changed[0]
            await unit_of_work.commit()
        if updated_deal is not None and self._deal_service is not None:
            try:
                await self._deal_service.publish_committed_status_change(
                    updated_deal, new_status=DealStatus.CANCELED,
                    payload={"reason": "exchange_cancelled_in_telegram"},
                )
            except Exception:
                log.exception("Failed to publish canceled exchange %s", command.request_id)
        return CancelExchangeTransactionResult(receive_sign, pay_sign, client_id)


def _mutable_codes(codes: frozenset[str] | None) -> set[str] | None:
    return set(codes) if codes is not None else None


class TradingService:
    def __init__(self, posting, reversals, queries: TradingReadPort, uow_factory):
        self._posting, self._reversals, self._queries = posting, reversals, queries
        self._commands = MetadataCommandService(uow_factory)
        self._factory = uow_factory

    async def create(
        self, *, key, actor_id, client_id, mode, receive, pay, external_reference=None, comment="",
        operation_type=None, presentation=None, work=None,
    ):
        attributes = await self._attributes(client_id, mode, receive, pay)
        if operation_type is not None and operation_type != attributes["operation_type"]:
            raise InvalidPosting("Тип обмена не соответствует валютной паре")
        if comment:
            attributes["comment"] = comment
        if presentation is not None:
            attributes["presentation"] = dict(presentation)
        reference = external_reference or f"thai:{key}"

        async def action(work):
            uow = cast(TradingUnitOfWork, work)
            if client_id is not None:
                await uow.clients.lock_client(client_id)
            return await uow.trades.create(actor_id, client_id, attributes, reference)

        return await self._commands.execute(
            key=key,
            actor_id=actor_id,
            kind="trade_create",
            payload={"client_id": client_id, "attributes": attributes, "reference": reference},
            action=action,
            permission="deal.post",
            work=work,
        )

    async def _attributes(self, client_id, mode, receive, pay):
        accounts = await self._queries.accounts(
            client_id, receive["instrument"], pay["instrument"], mode
        )
        attributes = {"mode": mode, "operation_type": exchange_type(receive["instrument"], pay["instrument"])}
        for side, leg in (("receive", receive), ("pay", pay)):
            quantity = exact(decimal(leg["quantity"]), accounts[side]["precision"])
            if quantity <= 0:
                raise InvalidPosting("Обе суммы сделки должны быть положительными")
            rate = None if leg.get("quoted_rate") is None else decimal(leg["quoted_rate"])
            if rate is not None:
                FxQuote(
                    accounts[side]["currency"],
                    rate,
                    "units_per_usdt" if accounts[side]["currency"] == "THB" else "usdt_per_unit",
                    leg["instrument"],
                )
            attributes[side] = {
                "instrument": leg["instrument"],
                "quantity": quantity,
                "quoted_rate": rate,
            }
        return attributes

    async def ready(self, *, key, actor_id, deal_id, work=None):
        return await self._commands.execute(
            key=key,
            actor_id=actor_id,
            kind="trade_ready",
            payload={"deal_id": deal_id},
            action=lambda work: cast(TradingUnitOfWork, work).trades.ready(deal_id),
            permission="deal.post",
            work=work,
        )

    async def create_and_post(self, *, key, actor_id, client_id, receive, pay,
                              business_date, effective_at, presentation, comment='', profit_revision_id=None):
        """Bot and CRM client exchanges share one atomic application command."""
        async with self._factory() as work:
            await work.journal.lock_posting_period(profit_revision_id=profit_revision_id)
            created = await self.create(key=key+':draft',actor_id=actor_id,client_id=client_id,
                mode='wallet',receive=receive,pay=pay,external_reference=key,
                comment=comment,presentation=presentation,work=work)
            current = await cast(TradingUnitOfWork,work).trades.lock(created['deal_id'])
            if current['status'] in ('draft','ready'):
                await self.ready(key=key+':ready',actor_id=actor_id,deal_id=current['id'],work=work)
            await self.post(key=key,actor_id=actor_id,deal_id=current['id'],business_date=business_date,
                effective_at=effective_at,profit_revision_id=profit_revision_id,work=work)
            await work.commit()
        return created['deal_id']

    async def cancel_draft(self, *, key, actor_id, deal_id, reason):
        if not reason.strip():
            raise InvalidPosting("Причина отмены обязательна")
        return await self._commands.execute(
            key=key,
            actor_id=actor_id,
            kind="trade_cancel",
            payload={"deal_id": deal_id, "reason": reason},
            action=lambda work: cast(TradingUnitOfWork, work).trades.cancel_draft(deal_id, reason),
            permission="deal.post",
        )

    async def post(self, *, key, actor_id, deal_id, business_date, effective_at, settlement=None, profit_revision_id=None, work=None):
        if settlement is not None:
            if settlement["block_ts"].tzinfo is None:
                raise InvalidPosting("Время подтверждённого перевода должно содержать часовой пояс")
            settlement = {**settlement, "amount": exact(decimal(settlement["amount"]), 6)}
        deal = await work.trades.lock(deal_id) if work is not None else await self._queries.detail(deal_id)
        if deal["kind"] in ("deposit", "withdrawal"):
            raise InvalidPosting("Кассовую заявку проводят фактической суммой через кассовый расчёт")
        attributes = deal["attributes"].get("contract", deal["attributes"])
        accounts = await self._queries.accounts(
            deal["client_id"],
            attributes["receive"]["instrument"],
            attributes["pay"]["instrument"],
            attributes["mode"],
        )
        mode = attributes["mode"]
        field = "cash" if mode == "cash" else "wallet"
        ids = tuple(
            sorted(
                {accounts[side][field] for side in ("receive", "pay")}
                | {accounts["trading_income"], accounts["trading_loss"]}
            )
        )
        command = OperationCommand(
            key,
            "fx_trade",
            actor_id,
            business_date,
            effective_at,
            ids,
            canonical_json(
                revision_details({"deal_id": deal_id, "attributes": attributes, "settlement": settlement,
                    **({'cash_request_id':deal_id} if deal['kind']=='cash_exchange' else {})}, profit_revision_id)
            ),
        )
        prepared = {}

        async def prepare(work):
            uow = cast(TradingUnitOfWork, work)
            if deal["client_id"] is not None:
                await uow.clients.lock_client(deal["client_id"])
            locked = await uow.trades.lock(deal_id)
            if locked["status"] != "ready" or locked["attributes"] != attributes:
                raise InvalidPosting("Провести можно только готовую неизменённую сделку")
            confirmations = {}
            if deal['kind']=='cash_exchange':
                confirmations = {row['direction']:row for row in await uow.trades.cash_confirmations(deal_id)}
                if set(confirmations)!={'receive','pay'}:
                    raise InvalidPosting('Сначала подтвердите обе стороны кассовой заявки')
            await uow.trades.guard_watch(deal_id, settlement)
            if settlement is not None and decimal(settlement["amount"]) != decimal(
                attributes["pay"]["quantity"]
            ):
                raise InvalidPosting(
                    "Подтверждённая сумма не совпадает с выдачей; требуется проверка менеджера"
                )
            for side in ("receive", "pay"):
                currency = accounts[side]["currency"]
                leg = attributes[side]
                rate = confirmations.get(side,{}).get('quoted_rate') or leg.get("quoted_rate")
                other = attributes['pay' if side=='receive' else 'receive']
                if rate is None and currency!='USDT' and other['instrument']=='USDT':
                    with localcontext() as calculation:
                        calculation.prec=80
                        rate=rounded(decimal(leg['quantity'])/decimal(other['quantity']) if currency=='THB'
                            else decimal(other['quantity'])/decimal(leg['quantity']),18)
                if rate is not None or currency == "USDT":
                    prepared[side] = FxQuote(currency, decimal(rate if rate is not None else 1),
                        "units_per_usdt" if currency == "THB" else "usdt_per_unit", leg["instrument"])
                else:
                    try:
                        prepared[side] = await uow.trades.quote(leg["instrument"], effective_at)
                    except MissingMarketQuote:
                        if mode != "wallet":
                            raise
            # A wallet exchange with a USDT leg already specifies its valuation.
            # Missing cross quotes can be derived from the two agreed native amounts.
            if len(prepared) == 1:
                known = next(iter(prepared))
                missing = "pay" if known == "receive" else "receive"
                with localcontext() as context:
                    context.prec = 80
                    total = prepared[known].value(decimal(attributes[known]["quantity"]))
                    quantity = decimal(attributes[missing]["quantity"])
                    currency = accounts[missing]["currency"]
                    rate = rounded(quantity / total if currency == "THB" else total / quantity, 18)
                prepared[missing] = FxQuote(currency, rate,
                    "units_per_usdt" if currency == "THB" else "usdt_per_unit", attributes[missing]["instrument"])
            if len(prepared) != 2:
                raise MissingMarketQuote("Для пары без USDT нужна оценка хотя бы одной валюты в USDT. Сначала проведите обмен с USDT для формирования внутреннего курса.")

        def build(state):
            source_qty, target_qty = (
                decimal(attributes["receive"]["quantity"]),
                decimal(attributes["pay"]["quantity"]),
            )
            if mode == "cash":
                incoming = prepared["receive"].value(source_qty)
                outgoing = state.position(attributes["pay"]["instrument"]).withdrawal_cost(
                    target_qty
                )
                lines = (
                    JournalLine(
                        accounts["receive"]["cash"],
                        accounts["receive"]["currency"],
                        Side.DEBIT,
                        source_qty,
                        incoming,
                        Valuation.RATE,
                        prepared["receive"],
                    ),
                    JournalLine(
                        accounts["pay"]["cash"],
                        accounts["pay"]["currency"],
                        Side.CREDIT,
                        target_qty,
                        outgoing,
                        Valuation.CARRYING,
                    ),
                )
            else:
                source, target = (
                    state.account(accounts["receive"]["wallet"]),
                    state.account(accounts["pay"]["wallet"]),
                )
                incoming = -liability_change(
                    -source.native, -source.usdt, -source_qty, prepared["receive"]
                )
                outgoing = liability_change(
                    -target.native, -target.usdt, target_qty, prepared["pay"]
                )
                lines = (
                    JournalLine(
                        source.id,
                        source.currency,
                        Side.DEBIT,
                        source_qty,
                        incoming,
                        Valuation.CARRYING,
                    ),
                    JournalLine(
                        target.id,
                        target.currency,
                        Side.CREDIT,
                        target_qty,
                        outgoing,
                        Valuation.CARRYING,
                    ),
                )
            prepared["result"] = incoming - outgoing
            return balance_with_fx(
                lines,
                income_account_id=accounts["trading_income"],
                loss_account_id=accounts["trading_loss"],
            )

        async def save(operation_id, uow, plan):
            final = {**attributes, "contract": attributes, "result_usdt": prepared["result"]}
            for side in ("receive", "pay"):
                q = prepared[side]
                final[side] = {
                    **attributes[side],
                    "quantity": decimal(attributes[side]["quantity"]),
                    "quoted_rate": q.quoted_rate,
                    "direction": q.direction,
                    "usdt_per_unit": q.usdt_per_unit,
                }
            await uow.trades.post(deal_id, operation_id, final)
            if settlement is not None:
                await uow.trades.settlement(deal_id, operation_id, settlement)

        return await self._posting.post(command, build, prepare=prepare, document=CallbackDocument(save), work=work,
            valuation_quotes=lambda state:tuple(prepared[side] for side in ("receive","pay")))

    async def reverse(
        self, *, key, actor_id, deal_id, business_date, effective_at, reason, replacement=None, profit_revision_id=None,
        work=None, before_reverse=None
    ):
        deal = await self._queries.detail(deal_id)
        if replacement is not None and deal["kind"] in ("deposit", "withdrawal", "cash_exchange"):
            raise InvalidPosting("После сторно создайте новую кассовую заявку")
        if replacement is not None:
            replacement_attributes = await self._attributes(
                deal["client_id"], replacement["mode"], replacement["receive"], replacement["pay"]
            )
            if replacement.get("comment"):
                replacement_attributes["comment"] = replacement["comment"]
            if replacement.get("presentation") is not None:
                replacement_attributes["presentation"] = replacement["presentation"]
            replacement = replacement_attributes
        if deal["operation_id"] is None:
            raise InvalidPosting("Черновик отменяется без финансового сторно")

        async def prepare(work):
            uow = cast(TradingUnitOfWork, work)
            if deal["client_id"] is not None:
                await uow.clients.lock_client(deal["client_id"])
            if before_reverse is not None:
                await before_reverse(uow)
            await uow.trades.lock(deal_id)
            await uow.trades.reversible(deal_id)

        async def save(operation_id, uow, plan):
            replacement_id = None
            if replacement is not None:
                # Replacement is a reviewed draft, not an implicit second money movement.
                replacement_id = (
                    await uow.trades.create(
                        actor_id, deal["client_id"], replacement, f"correction:{key}"
                    )
                )["deal_id"]
            await uow.trades.cancelled(deal_id, operation_id, reason, replacement_id)

        return await self._reversals.reverse(
            original_id=deal["operation_id"],
            key=key,
            actor_id=actor_id,
            business_date=business_date,
            effective_at=effective_at,
            reason=reason,
            prepare=prepare,
            details=revision_details({"deal_id": deal_id, "replacement": replacement}, profit_revision_id),
            document=CallbackDocument(save),
            work=work,
        )

    async def correct(self, *, key, actor_id, deal_id, replacement, business_date, effective_at,
                      reason, expected_deal_id=None, profit_revision_id=None):
        context = await self._queries.exchange_context(deal_id)
        root_id = context['root']['id']
        # The command describes the stable request; a retry must not target its own replacement.
        attributes = await self._attributes(context['root']['client_id'], replacement['mode'],
            replacement['receive'], replacement['pay'])
        for name in ('comment', 'presentation'):
            if name in replacement:
                attributes[name] = replacement[name]
        payload = {'root_id':root_id, 'replacement':attributes, 'business_date':business_date,
            'effective_at':effective_at, 'reason':reason, 'profit_revision_id':profit_revision_id}
        async def action(work):
            uow = cast(TradingUnitOfWork, work)
            async def validate_current(locked):
                # Reversal guards acquire period/key locks before the client and request locks.
                await locked.trades.lock(root_id)
                current = await locked.trades.exchange_current(root_id)
                if (current['status']!='posted' or current['id']!=context['active']['id']
                    or (expected_deal_id is not None and current['id']!=expected_deal_id)):
                    raise InvalidPosting('Заявка изменена или отменена; обновите карточку')
            reversed_operation = await self.reverse(key=key+':reversal', actor_id=actor_id,
                deal_id=context['active']['id'], replacement=attributes, business_date=business_date,
                effective_at=effective_at, reason=reason, profit_revision_id=profit_revision_id, work=uow,
                before_reverse=validate_current)
            current = await uow.trades.exchange_current(root_id)
            await uow.trades.ready(current['id'])
            posted = await self.post(key=key+':replacement', actor_id=actor_id, deal_id=current['id'],
                business_date=business_date, effective_at=effective_at, profit_revision_id=profit_revision_id, work=uow)
            return {'deal_id':current['id'], 'request_id':root_id, 'operation_id':posted.id,
                'reversal_operation_id':reversed_operation.id}
        return await self._commands.execute(key=key, actor_id=actor_id, kind='trade_correct',
            payload=payload, action=action, permission='deal.post')

    async def watch(self, *, key, actor_id, deal_id, chat_id, address, our_address, timeout_at):
        return await self._commands.execute(
            key=key,
            actor_id=actor_id,
            kind="trade_watch",
            payload={
                "deal_id": deal_id,
                "chat_id": chat_id,
                "address": address,
                "our_address": our_address,
                "timeout_at": timeout_at,
            },
            action=lambda work: cast(TradingUnitOfWork, work).trades.watch(
                deal_id, actor_id, chat_id, address, our_address, timeout_at
            ),
            permission="deal.post",
        )

