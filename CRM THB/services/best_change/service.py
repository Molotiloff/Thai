from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Protocol, Self
from zoneinfo import ZoneInfo

from domain import DomainValidationError
from domain.accounting_policy import calculate_best_change
from domain.finance.fx_result import balance_with_fx
from domain.finance.models import (
    FxQuote,
    InvalidPosting,
    JournalLine,
    OperationCommand,
    PostingMode,
    PostingPlan,
    Side,
    Valuation,
    canonical_json,
    revision_details,
)
from domain.wallet_valuation import liability_change
from services.accounting.ports import ProfitReadPort
from services.finance.posting import CallbackDocument, base_line
from services.metadata_commands import MetadataCommandService

from .calculator import BestChangeCalculator
from .models import (
    BestChangeAccount,
    BestChangeCalculation,
    BestChangeCorrectionResult,
    BestChangeMonthCloseResult,
    BestChangeMonthReport,
    BestChangeMonthReversalResult,
    BestChangePaymentResult,
    BestChangePaymentReversalResult,
    BestChangePostingResult,
    CloseBestChangeMonth,
    CorrectBestChangeDeal,
    RecordBestChangeDeal,
    RecordBestChangePayment,
    ReverseBestChangeMonth,
    ReverseBestChangePayment,
)


class BestChangeRepositoryPort(Protocol):
    async def post_deal(
        self,
        *,
        command: RecordBestChangeDeal,
        calculation: BestChangeCalculation,
    ) -> BestChangePostingResult: ...

    async def balances(self, *, chat_id: int) -> dict[BestChangeAccount, Decimal]: ...

    async def month_report(
        self,
        *,
        chat_id: int,
        period_month: date,
    ) -> BestChangeMonthReport: ...

    async def close_month(
        self,
        *,
        command: CloseBestChangeMonth,
    ) -> BestChangeMonthCloseResult: ...

    async def record_payment(
        self,
        *,
        command: RecordBestChangePayment,
    ) -> BestChangePaymentResult: ...

    async def reverse_payment(
        self,
        *,
        command: ReverseBestChangePayment,
    ) -> BestChangePaymentReversalResult: ...

    async def reverse_month(
        self,
        *,
        command: ReverseBestChangeMonth,
    ) -> BestChangeMonthReversalResult: ...

    async def prepare_correction(
        self,
        *,
        command: CorrectBestChangeDeal,
    ) -> date: ...

    async def correct_deal(
        self,
        *,
        command: CorrectBestChangeDeal,
        replacement: RecordBestChangeDeal,
        calculation: BestChangeCalculation,
    ) -> BestChangeCorrectionResult: ...


class BestChangeUnitOfWorkPort(Protocol):
    @property
    def best_change(self) -> BestChangeRepositoryPort: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...


BestChangeUnitOfWorkFactory = Callable[[], BestChangeUnitOfWorkPort]


class BestChangeService:
    def __init__(
        self,
        unit_of_work_factory: BestChangeUnitOfWorkFactory,
        *,
        chat_id: int,
        today_factory: Callable[[], date] = date.today,
    ) -> None:
        if chat_id == 0:
            raise ValueError("BestChange chat id must not be zero")
        self._unit_of_work_factory = unit_of_work_factory
        self._chat_id = chat_id
        self._today = today_factory

    async def record(self, command: RecordBestChangeDeal) -> BestChangePostingResult:
        self._validate_chat(command.chat_id, "command")
        if command.deal_at > self._today():
            raise DomainValidationError("BestChange deal date cannot be in the future")
        calculation = BestChangeCalculator.calculate(command)
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.best_change.post_deal(
                command=command,
                calculation=calculation,
            )
            await unit_of_work.commit()
            return result

    async def balances(self) -> dict[BestChangeAccount, Decimal]:
        async with self._unit_of_work_factory() as unit_of_work:
            balances = await unit_of_work.best_change.balances(chat_id=self._chat_id)
            await unit_of_work.commit()
            return balances

    async def month_report(self, period_month: date) -> BestChangeMonthReport:
        _validate_period_month(period_month)
        async with self._unit_of_work_factory() as unit_of_work:
            report = await unit_of_work.best_change.month_report(
                chat_id=self._chat_id,
                period_month=period_month,
            )
            await unit_of_work.commit()
            return report

    async def close_month(
        self,
        command: CloseBestChangeMonth,
    ) -> BestChangeMonthCloseResult:
        self._validate_chat(command.chat_id, "close")
        _validate_period_month(command.period_month)
        today = self._today()
        current_month = today.replace(day=1)
        if command.period_month >= current_month:
            raise DomainValidationError("Only a completed BestChange month can be closed")
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.best_change.close_month(command=command)
            await unit_of_work.commit()
            return result

    async def record_payment(
        self,
        command: RecordBestChangePayment,
    ) -> BestChangePaymentResult:
        self._validate_chat(command.chat_id, "payment")
        _validate_period_month(command.period_month)
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.best_change.record_payment(command=command)
            await unit_of_work.commit()
            return result

    async def reverse_payment(
        self,
        command: ReverseBestChangePayment,
    ) -> BestChangePaymentReversalResult:
        self._validate_chat(command.chat_id, "reversal")
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.best_change.reverse_payment(command=command)
            await unit_of_work.commit()
            return result

    async def reverse_month(
        self,
        command: ReverseBestChangeMonth,
    ) -> BestChangeMonthReversalResult:
        self._validate_chat(command.chat_id, "reversal")
        _validate_period_month(command.period_month)
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.best_change.reverse_month(command=command)
            await unit_of_work.commit()
            return result

    async def correct_deal(
        self,
        command: CorrectBestChangeDeal,
    ) -> BestChangeCorrectionResult:
        self._validate_chat(command.chat_id, "correction")
        async with self._unit_of_work_factory() as unit_of_work:
            deal_at = await unit_of_work.best_change.prepare_correction(command=command)
            replacement = command.replacement(deal_at=deal_at)
            calculation = BestChangeCalculator.calculate(replacement)
            result = await unit_of_work.best_change.correct_deal(
                command=command,
                replacement=replacement,
                calculation=calculation,
            )
            await unit_of_work.commit()
            return result

    def _validate_chat(self, chat_id: int, operation: str) -> None:
        if chat_id != self._chat_id:
            raise DomainValidationError(
                f"BestChange {operation} is allowed only in its configured chat"
            )


def _validate_period_month(period_month: date) -> None:
    if period_month.day != 1:
        raise DomainValidationError("BestChange period must be the first day of a month")


class BestChangeLedgerService:
    def __init__(self, posting, queries: ProfitReadPort, trading_queries, *, uow_factory=None, profit=None):
        self._factory = uow_factory
        self._commands = MetadataCommandService(uow_factory) if uow_factory else None
        self._profit = profit
        self._posting, self._queries, self._trading = (
            posting,
            queries,
            trading_queries,
        )

    async def configure(
        self, *, key, actor_id, business_date, effective_at, buffer_client_id, daniyar_client_id, work=None
    ):
        command = OperationCommand(
            key,
            "profit_configure",
            actor_id,
            business_date,
            effective_at,
            (),
            canonical_json(
                {"buffer_client_id": buffer_client_id, "daniyar_client_id": daniyar_client_id}
            ),
            mode=PostingMode.MEMORANDUM,
        )

        async def prepare(work):
            await work.profit.permission(actor_id, "terms.manage")
            for client_id in sorted({buffer_client_id, daniyar_client_id}):
                await work.clients.lock_client(client_id)

        async def save(operation_id, work, plan):
            await work.profit.configure(operation_id, buffer_client_id, daniyar_client_id)

        return await self._posting.post(
            command,
            lambda state: PostingPlan((), PostingMode.MEMORANDUM),
            prepare=prepare,
            document=CallbackDocument(save),
            work=work,
        )

    async def configure_from_chats(self, *, actor_id, best_change_chat_id, daniyar_chat_id):
        """Resolve env chats through the original client registration and binding rules."""
        if not best_change_chat_id or not daniyar_chat_id or best_change_chat_id==daniyar_chat_id:
            raise InvalidPosting('Нужны два разных чата BEST_CHANGE_CHAT_ID и DANIYAR_CHAT')
        if self._factory is None:
            raise RuntimeError('Business chat configuration requires a unit of work')
        key=f'env:business-chats:{best_change_chat_id}:{daniyar_chat_id}'
        async with self._factory() as work:
            await work.journal.lock_posting_period()
            await work.clients.guard_directory(actor_id,key,'terms.manage')
            clients={}
            names={best_change_chat_id:'BestChange',daniyar_chat_id:'Баланс Данияр'}
            for chat_id in sorted(names):
                clients[chat_id]=await work.clients.ensure_chat_client(chat_id,names[chat_id],preserve_name=True)
            desired={'best_change_buffer':clients[best_change_chat_id],'daniyar_balance':clients[daniyar_chat_id]}
            if await work.profit.binding_clients()!=desired:
                now=datetime.now(UTC)
                await self.configure(key=key,actor_id=actor_id,effective_at=now,
                    business_date=now.astimezone(ZoneInfo('Asia/Bangkok')).date(),
                    buffer_client_id=desired['best_change_buffer'],daniyar_client_id=desired['daniyar_balance'],work=work)
            await work.commit()
            return desired

    async def record(
        self,
        *,
        key,
        actor_id,
        business_date,
        effective_at,
        operation,
        quantity,
        market_rate,
        client_rate,
        thb_instrument="THB_CASH",
        mode="cash",
        client_id=None,
        external_reference=None,
        correction_of=None,
        profit_revision_id=None,
        work=None,
    ):
        if thb_instrument not in ("THB_CASH", "THB_BANK"):
            raise InvalidPosting("BestChange работает в THB: выберите наличный/безналичный бат")
        result = calculate_best_change(operation, quantity, market_rate, client_rate)
        source, target = (
            (thb_instrument, "USDT") if operation == "sale" else ("USDT", thb_instrument)
        )
        qty_in, qty_out = (
            (result.thb_quantity, result.quantity)
            if operation == "sale"
            else (result.quantity, result.thb_quantity)
        )
        quote_in = (
            FxQuote("THB", result.market_rate, "units_per_usdt", thb_instrument)
            if operation == "sale"
            else FxQuote("USDT", Decimal(1), instrument="USDT")
        )
        quote_out = (
            FxQuote("USDT", Decimal(1), instrument="USDT")
            if operation == "sale"
            else FxQuote("THB", result.market_rate, "units_per_usdt", thb_instrument)
        )
        accounts = await self._queries.accounts()
        trading = await self._trading.accounts(client_id, source, target, mode)
        bindings = await self._queries.bindings()
        field = "cash" if mode == "cash" else "wallet"
        ids = {
            trading["receive"][field],
            trading["pay"][field],
            bindings["best_change_buffer"]["ledger_account_id"],
            accounts["trading_income"],
            accounts["trading_loss"],
            accounts["platform_commission_expense"],
            accounts["platform_commission_payable"],
            accounts["fx_income"],
            accounts["fx_loss"],
        }
        payload = revision_details(
            {
                "actor_id": actor_id,
                "client_id": client_id,
                "operation": operation,
                "quantity": result.quantity,
                "market_rate": result.market_rate,
                "client_rate": result.client_rate,
                "thb_instrument": thb_instrument,
                "mode": mode,
                "reference": external_reference or f"thai:{key}",
                "correction_of": correction_of,
            },
            profit_revision_id,
        )
        command = OperationCommand(
            key,
            "best_change",
            actor_id,
            business_date,
            effective_at,
            tuple(sorted(ids)),
            canonical_json(payload),
        )

        async def prepare(work):
            await work.profit.permission(actor_id, "deal.post")
            client_ids = {b["client_id"] for b in bindings.values()}
            if client_id is not None:
                client_ids.add(client_id)
            for identity in sorted(client_ids):
                await work.clients.lock_client(identity)
            if await work.profit.bindings() != bindings:
                raise InvalidPosting("Привязки BestChange изменились")
            if correction_of is not None:
                await work.profit.correction(correction_of)

        def build(state):
            if mode == "cash":
                incoming = quote_in.value(qty_in)
                outgoing = state.position(target).withdrawal_cost(qty_out)
            else:
                original, destination = (
                    state.account(trading["receive"][field]),
                    state.account(trading["pay"][field]),
                )
                incoming = -liability_change(-original.native, -original.usdt, -qty_in, quote_in)
                outgoing = liability_change(
                    -destination.native, -destination.usdt, qty_out, quote_out
                )
            lines = [
                JournalLine(
                    trading["receive"][field],
                    quote_in.currency,
                    Side.DEBIT,
                    qty_in,
                    incoming,
                    Valuation.CARRYING,
                ),
                JournalLine(
                    trading["pay"][field],
                    quote_out.currency,
                    Side.CREDIT,
                    qty_out,
                    outgoing,
                    Valuation.CARRYING,
                ),
            ]
            gross = result.gross_usdt
            if gross:
                lines.append(
                    base_line(
                        accounts["trading_income"] if gross > 0 else accounts["trading_loss"],
                        Side.CREDIT if gross > 0 else Side.DEBIT,
                        gross.copy_abs(),
                    )
                )
            if result.fee_usdt:
                lines += [
                    base_line(accounts["platform_commission_expense"], Side.DEBIT, result.fee_usdt),
                    base_line(
                        accounts["platform_commission_payable"], Side.CREDIT, result.fee_usdt
                    ),
                ]
            dany = result.daniyar_usdt
            if dany:
                lines += [
                    base_line(
                        accounts["trading_income"] if dany > 0 else accounts["trading_loss"],
                        Side.DEBIT if dany > 0 else Side.CREDIT,
                        dany.copy_abs(),
                    ),
                    base_line(
                        bindings["best_change_buffer"]["ledger_account_id"],
                        Side.CREDIT if dany > 0 else Side.DEBIT,
                        dany.copy_abs(),
                    ),
                ]
            return balance_with_fx(
                tuple(lines),
                income_account_id=accounts["fx_income"],
                loss_account_id=accounts["fx_loss"],
            )

        async def save(operation_id, work, plan):
            attributes = {
                "mode": mode,
                "calculation": asdict(result),
                "receive": {
                    "instrument": source,
                    "quantity": qty_in,
                    "quoted_rate": quote_in.quoted_rate,
                    "direction": quote_in.direction,
                    "usdt_per_unit": quote_in.usdt_per_unit,
                },
                "pay": {
                    "instrument": target,
                    "quantity": qty_out,
                    "quoted_rate": quote_out.quoted_rate,
                    "direction": quote_out.direction,
                    "usdt_per_unit": quote_out.usdt_per_unit,
                },
            }
            await work.profit.best_change(operation_id, payload, result, bindings, attributes)

        return await self._posting.post(
            command, build, prepare=prepare, document=CallbackDocument(save), work=work,valuation_quotes=(quote_in,quote_out)
        )



    async def correct(self, *, key, actor_id, deal_id, expected_deal_id, business_date,
                      effective_at, reason, replacement, profit_revision_id=None):
        if not reason.strip():
            raise InvalidPosting('Причина исправления обязательна')
        context = await self._queries.best_change_context(deal_id)
        root = context['root_id']
        async def action(work):
            await work.journal.lock_posting_period(profit_revision_id=profit_revision_id)
            await work.profit.lock_best_change(root)
            current = await work.profit.best_change_context(root)
            if current['active']['deal_id']!=expected_deal_id or not current['active']['active']:
                raise InvalidPosting('Сделка BestChange уже изменена или отменена')
            original = current['active']
            bindings = await work.profit.bindings()
            for cid in sorted({b['client_id'] for b in bindings.values()} | ({original['client_id']} if original['client_id'] else set())):
                await work.clients.lock_client(cid)
            await self._profit.reverse(key=key+':reverse',actor_id=actor_id,
                business_date=business_date,effective_at=effective_at,operation_id=original['operation_id'],
                reason=reason,profit_revision_id=profit_revision_id,work=work)
            posted = await self.record(key=key+':replacement',actor_id=actor_id,
                business_date=business_date,effective_at=effective_at,client_id=original['client_id'],
                mode=original['attributes']['mode'],correction_of=original['deal_id'],
                external_reference=key,profit_revision_id=profit_revision_id,work=work,**replacement)
            return {'root_id':root,'operation_id':posted.id}
        return await self._commands.execute(key=key,actor_id=actor_id,kind='best_change_correct',
            permission='finance.reverse',payload={'root':root,'expected':expected_deal_id,'replacement':replacement,
                'reason':reason,'business_date':business_date,'effective_at':effective_at,'profit_revision_id':profit_revision_id},action=action)
