from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from domain import DomainStateError, DomainValidationError
from domain.accounting_policy import payout_fifo, summarize
from domain.finance.fx_result import balance_with_fx
from domain.finance.models import (
    FxQuote,
    InvalidPosting,
    JournalLine,
    LedgerContext,
    OperationCommand,
    PostingMode,
    PostingPlan,
    Side,
    Valuation,
    allocate_percentages,
    canonical_json,
    decimal,
    exact,
    revision_details,
)
from services.accounting.ports import ProfitReadPort
from services.expenses.service import month
from services.finance.posting import CallbackDocument, base_line
from services.unit_of_work import UnitOfWorkFactory

from .firm_position_service import FirmPositionAccountingService
from .models import (
    AccrueUsdtProfit,
    ManualProfitQuote,
    MarketQuote,
    ProfitAccrual,
    ProfitCapitalizationResult,
    RecordProfitCapitalization,
)
from .ports import MarketQuoteProviderPort


class ProfitValuationService:
    def __init__(
        self,
        quote_provider: MarketQuoteProviderPort,
        *,
        max_quote_age: timedelta = timedelta(minutes=5),
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._quote_provider = quote_provider
        self._max_quote_age = max_quote_age
        self._now = now_factory or (lambda: datetime.now(UTC))

    async def resolve(
        self,
        symbol: str,
        *,
        manual: ManualProfitQuote | None = None,
    ) -> tuple[MarketQuote, int | None, str | None]:
        if manual is not None:
            price = self._positive(manual.price, "manual valuation price")
            reason = manual.reason.strip()
            if manual.actor_user_id <= 0:
                raise DomainValidationError("Manual valuation actor is required")
            if not reason:
                raise DomainValidationError("Manual valuation reason is required")
            if manual.observed_at.tzinfo is None:
                raise DomainValidationError("Manual valuation time must be timezone-aware")
            return (
                MarketQuote(
                    price=price,
                    source="manual",
                    symbol=symbol,
                    observed_at=manual.observed_at,
                ),
                manual.actor_user_id,
                reason,
            )
        quote = await self._quote_provider.best_bid(symbol)
        if quote is None:
            raise DomainStateError("Market quote is unavailable")
        if quote.symbol != symbol:
            raise DomainStateError("Market quote symbol does not match request")
        if quote.observed_at.tzinfo is None:
            raise DomainStateError("Market quote time must be timezone-aware")
        age = self._now() - quote.observed_at
        if age < timedelta(0) or age > self._max_quote_age:
            raise DomainStateError("Market quote is stale")
        self._positive(quote.price, "market quote price")
        return quote, None, None

    @staticmethod
    def _positive(value: object, field: str) -> Decimal:
        try:
            parsed = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise DomainValidationError(f"Invalid {field}") from None
        if not parsed.is_finite() or parsed <= 0:
            raise DomainValidationError(f"{field.capitalize()} must be positive")
        return parsed


class ProfitAccrualService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def accrue(self, command: AccrueUsdtProfit) -> ProfitAccrual:
        qty = ProfitValuationService._positive(command.qty, "profit quantity")
        key = command.idempotency_key.strip()
        if not key:
            raise DomainValidationError("Profit idempotency key is required")
        async with self._unit_of_work_factory() as unit_of_work:
            existing = await unit_of_work.profit_accruals.get_by_idempotency_key(key)
            if existing is not None:
                if (
                    existing.deal_id != command.deal_id
                    or existing.qty != qty
                    or existing.settlement_status is not command.settlement_status
                ):
                    raise DomainStateError("Idempotency key belongs to another profit accrual")
                return existing
            deal = await unit_of_work.deals.get_deal(command.deal_id)
            if deal is None:
                raise DomainValidationError("Profit deal was not found")
            result = await unit_of_work.profit_accruals.append(
                deal_id=command.deal_id,
                qty=qty,
                settlement_status=command.settlement_status.value,
                idempotency_key=key,
            )
            await unit_of_work.commit()
            return result

    async def mark_received(
        self,
        *,
        accrual_id: int,
        payment_event_id: int,
        received_at: datetime,
    ) -> ProfitAccrual:
        if received_at.tzinfo is None:
            raise DomainValidationError("Profit receipt time must be timezone-aware")
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.profit_accruals.mark_received(
                accrual_id=accrual_id,
                payment_event_id=payment_event_id,
                received_at=received_at,
            )
            await unit_of_work.commit()
            return result


class MidnightProfitCapitalizationJob:
    SYMBOL = "USDT/RUB"

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        valuation_service: ProfitValuationService,
        position_service: FirmPositionAccountingService,
        timezone: str = "Asia/Yekaterinburg",
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._valuation_service = valuation_service
        self._position_service = position_service
        self._timezone = ZoneInfo(timezone)
        self._now = now_factory or (lambda: datetime.now(UTC))

    async def run_previous_day(self) -> ProfitCapitalizationResult:
        business_date = self._now().astimezone(self._timezone).date() - timedelta(days=1)
        return await self.run(business_date)

    async def run(
        self,
        business_date: date,
        *,
        manual_quote: ManualProfitQuote | None = None,
    ) -> ProfitCapitalizationResult:
        boundary = datetime.combine(
            business_date + timedelta(days=1),
            time.min,
            tzinfo=self._timezone,
        )
        key = f"profit-capitalization:{business_date.isoformat()}"
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.profit_accruals.acquire_capitalization_lock(business_date)
            existing_move = await unit_of_work.firm_positions.get_by_idempotency_key(key)
            if existing_move is not None:
                return ProfitCapitalizationResult(
                    business_date=business_date,
                    qty=existing_move.qty,
                    rub_value=existing_move.rub_amount,
                    accrual_count=0,
                    position_move_id=existing_move.id,
                    repeated=True,
                )
            accruals = await unit_of_work.profit_accruals.list_pending_before(boundary)
            if not accruals:
                await unit_of_work.commit()
                return ProfitCapitalizationResult(
                    business_date=business_date,
                    qty=Decimal(0),
                    rub_value=Decimal(0),
                    accrual_count=0,
                    position_move_id=None,
                    repeated=False,
                )
            quote, actor_user_id, reason = await self._valuation_service.resolve(
                self.SYMBOL,
                manual=manual_quote,
            )
            qty = sum((item.qty for item in accruals), Decimal(0))
            capitalized_at = self._now()
            move = await self._position_service.record_profit_capitalization(
                RecordProfitCapitalization(
                    currency="USDT",
                    qty=qty,
                    rate=quote.price,
                    deal_id=None,
                    actor_user_id=actor_user_id,
                    effective_at=boundary,
                    idempotency_key=key,
                ),
                unit_of_work=unit_of_work,
            )
            await unit_of_work.profit_accruals.mark_capitalized(
                accrual_ids=tuple(item.id for item in accruals),
                quote=quote,
                move_id=move.id,
                capitalized_at=capitalized_at,
                actor_user_id=actor_user_id,
                reason=reason,
            )
            await unit_of_work.commit()
            return ProfitCapitalizationResult(
                business_date=business_date,
                qty=qty,
                rub_value=qty * quote.price,
                accrual_count=len(accruals),
                position_move_id=move.id,
                repeated=False,
            )


class ProfitService:
    def __init__(self, posting, reversals, queries: ProfitReadPort):
        self._posting, self._reversals, self._queries = posting, reversals, queries

    async def set_terms(
        self,
        *,
        key,
        actor_id,
        business_date,
        effective_at,
        valid_from,
        skyex_percent,
        pe_percent,
        reason,
        profit_revision_id=None,
    ):
        skyex_percent, pe_percent = exact(decimal(skyex_percent), 6), exact(decimal(pe_percent), 6)
        if min(skyex_percent, pe_percent) < 0 or skyex_percent + pe_percent != 100:
            raise InvalidPosting("Доли собственников должны составлять 100%")
        payload = revision_details(
            {
                "valid_from": valid_from,
                "skyex_percent": skyex_percent,
                "pe_percent": pe_percent,
                "reason": reason,
            },
            profit_revision_id,
        )
        command = OperationCommand(
            key,
            "profit_terms",
            actor_id,
            business_date,
            effective_at,
            (),
            canonical_json(payload),
            mode=PostingMode.MEMORANDUM,
        )

        async def prepare(work):
            await work.profit.permission(actor_id, "terms.manage")

        async def save(operation_id, work, plan):
            await work.profit.set_term(
                operation_id,
                actor_id,
                valid_from,
                skyex_percent,
                pe_percent,
                reason,
                profit_revision_id,
            )

        return await self._posting.post(
            command,
            lambda state: PostingPlan((), PostingMode.MEMORANDUM),
            prepare=prepare,
            document=CallbackDocument(save),
        )

    async def close(self, *, key, actor_id, period, effective_at, profit_revision_id=None):
        month(period)
        last_day = date(period.year, period.month, monthrange(period.year, period.month)[1])
        if (
            effective_at.tzinfo is None
            or effective_at.astimezone(ZoneInfo("Asia/Bangkok")).date() <= last_day
        ):
            raise InvalidPosting("Прибыль месяца закрывается после его окончания")
        accounts = await self._queries.accounts()
        # Account registry is stable; include all P&L accounts, not just currently nonzero sources.
        pnl = await self._queries.pnl_accounts()
        bindings = await self._queries.optional_bindings()
        ids = {row["id"] for row in pnl} | {
            accounts["retained_result"],
            *accounts["owners"].values(),
        }
        if bindings:
            ids |= {b["ledger_account_id"] for b in bindings.values()}
        command = OperationCommand(
            key,
            "profit_closure",
            actor_id,
            period,
            effective_at,
            tuple(sorted(ids)),
            canonical_json(revision_details({"period": period}, profit_revision_id)),
            context=LedgerContext.PROFIT,
            mode=PostingMode(await self._queries.closure_mode(key, period)),
        )
        prepared = {}

        async def prepare(work):
            await work.profit.permission(
                actor_id, "period.revise" if profit_revision_id else "period.close"
            )
            await work.profit.prepare_close(period, profit_revision_id)
            sources = await work.profit.sources(period)
            report = summarize(sources)
            term = await work.profit.term(period)
            distributions = allocate_percentages(
                max(report["net_profit_usdt"], Decimal(0)),
                (
                    (accounts["participants"]["skyex"], term["skyex_percent"]),
                    (accounts["participants"]["pe"], term["pe_percent"]),
                ),
                8,
            )
            transfers = await work.profit.transferable(period)
            if transfers and bindings is None:
                raise InvalidPosting("Для закрытия BestChange необходимы привязки клиентов")
            if bindings:
                for identity in sorted({b["client_id"] for b in bindings.values()}):
                    await work.clients.lock_client(identity)
            prepared.update(
                sources=sources,
                report=report,
                term=term,
                distributions=distributions,
                transfers=transfers,
            )

        def build(state):
            totals = {}
            for row in prepared["sources"]:
                totals[row["account_id"]] = (
                    totals.get(row["account_id"], Decimal(0)) + row["credit_signed_usdt"]
                )
            lines = [
                base_line(account, Side.DEBIT if value > 0 else Side.CREDIT, value.copy_abs())
                for account, value in totals.items()
                if value
            ]
            net = prepared["report"]["net_profit_usdt"]
            if net:
                lines.append(
                    base_line(
                        accounts["retained_result"],
                        Side.CREDIT if net > 0 else Side.DEBIT,
                        net.copy_abs(),
                    )
                )
            for party_id, value in prepared["distributions"]:
                if value:
                    lines += [
                        base_line(accounts["retained_result"], Side.DEBIT, value),
                        base_line(accounts["owners"][party_id], Side.CREDIT, value),
                    ]
            transferred = sum(row["daniyar_share_usdt"] for row in prepared["transfers"])
            if transferred:
                lines += [
                    base_line(
                        bindings["best_change_buffer"]["ledger_account_id"],
                        Side.DEBIT if transferred > 0 else Side.CREDIT,
                        transferred.copy_abs(),
                    ),
                    base_line(
                        bindings["daniyar_balance"]["ledger_account_id"],
                        Side.CREDIT if transferred > 0 else Side.DEBIT,
                        transferred.copy_abs(),
                    ),
                ]
            # Empty months still need an immutable closure; handled by an explicit memorandum mode below.
            return PostingPlan(tuple(lines), command.mode)

        async def save(operation_id, work, plan):
            await work.profit.close(
                operation_id,
                period,
                profit_revision_id,
                prepared["report"],
                prepared["term"],
                prepared["sources"],
                prepared["distributions"],
                prepared["transfers"],
                bindings,
            )
            for source_id in await work.profit.dependency_sources(
                {row["operation_id"] for row in prepared["sources"]}
                | {row["operation_id"] for row in prepared["transfers"]}
            ):
                await work.journal.add_dependency(source_id, operation_id)

        return await self._posting.post(
            command, build, prepare=prepare, document=CallbackDocument(save)
        )

    async def pay(
        self,
        *,
        key,
        actor_id,
        business_date,
        effective_at,
        recipient,
        instrument,
        quantity,
        quoted_rate=None,
        profit_revision_id=None,
    ):
        accounts = await self._queries.accounts()
        platform = recipient == "platform"
        if not platform and recipient not in ("skyex", "pe"):
            raise InvalidPosting("Получатель выплаты: skyex, pe или platform")
        instrument_row = await self._queries.instrument(instrument)
        quantity = exact(decimal(quantity), instrument_row["precision"])
        if quantity <= 0:
            raise InvalidPosting("Количество выплаты должно быть положительным")
        if instrument != "USDT" and quoted_rate is None:
            internal = await self._queries.internal_quote(instrument,effective_at,key)
            if internal is None:
                raise InvalidPosting("Внутренний курс валюты ещё не сформирован обменами")
            quoted_rate = internal.quoted_rate
        quote = FxQuote(
            instrument_row["currency_code"],
            decimal(quoted_rate if quoted_rate is not None else 1),
            "units_per_usdt" if instrument_row["currency_code"] == "THB" else "usdt_per_unit",
            instrument,
        )
        amount = quote.value(quantity)
        recipient_id = (
            accounts["platform_party_id"] if platform else accounts["participants"][recipient]
        )
        payable = (
            accounts["platform_commission_payable"]
            if platform
            else accounts["owners"][recipient_id]
        )
        ids = tuple(
            sorted({payable, instrument_row["cash"], accounts["fx_income"], accounts["fx_loss"]})
        )
        command = OperationCommand(
            key,
            "platform_payment" if platform else "profit_payment",
            actor_id,
            business_date,
            effective_at,
            ids,
            canonical_json(
                revision_details(
                    {
                        "recipient": recipient,
                        "instrument": instrument,
                        "quantity": quantity,
                        "rate": quote.quoted_rate,
                    },
                    profit_revision_id,
                )
            ),
        )
        prepared = {}

        async def prepare(work):
            await work.profit.permission(actor_id, "deal.post" if platform else "period.close")
            prepared["allocations"] = payout_fifo(
                await work.profit.unpaid(recipient_id, platform, business_date), amount
            )

        def build(state):
            cost = state.position(instrument).withdrawal_cost(quantity)
            return balance_with_fx(
                (
                    base_line(payable, Side.DEBIT, amount),
                    JournalLine(
                        instrument_row["cash"],
                        quote.currency,
                        Side.CREDIT,
                        quantity,
                        cost,
                        Valuation.CARRYING,
                    ),
                ),
                income_account_id=accounts["fx_income"],
                loss_account_id=accounts["fx_loss"],
            )

        async def save(operation_id, work, plan):
            await work.profit.payment(
                operation_id,
                recipient_id,
                instrument,
                quantity,
                amount,
                quote,
                actor_id,
                effective_at,
                prepared["allocations"],
                platform,
            )
            for source in sorted({row[1] for row in prepared["allocations"]}):
                await work.journal.add_dependency(source, operation_id)

        return await self._posting.post(
            command, build, prepare=prepare, document=CallbackDocument(save),valuation_quotes=(quote,)
        )

    async def reverse(
        self,
        *,
        key,
        actor_id,
        business_date,
        effective_at,
        operation_id,
        reason,
        profit_revision_id=None,
        work=None,
    ):
        async def prepare(work):
            await work.profit.permission(actor_id, "deal.post")
            await work.profit.reversible(operation_id)

        async def save(reverse_id, work, plan):
            await work.profit.reversed(operation_id, reverse_id, reason)

        return await self._reversals.reverse(
            original_id=operation_id,
            key=key,
            actor_id=actor_id,
            business_date=business_date,
            effective_at=effective_at,
            reason=reason,
            prepare=prepare,
            details=revision_details({}, profit_revision_id),
            document=CallbackDocument(save),
            work=work,
        )

    async def start_revision(self, *, key, actor_id, business_date, effective_at, period, reason):
        month(period)
        command = OperationCommand(
            key,
            "profit_revision",
            actor_id,
            business_date,
            effective_at,
            (),
            canonical_json({"period": period, "reason": reason}),
            mode=PostingMode.MEMORANDUM,
        )

        async def prepare(work):
            await work.profit.permission(actor_id, "period.revise")

        async def save(operation_id, work, plan):
            await work.profit.start_revision(operation_id, period, reason)

        return await self._posting.post(
            command,
            lambda state: PostingPlan((), PostingMode.MEMORANDUM),
            prepare=prepare,
            document=CallbackDocument(save),
        )

