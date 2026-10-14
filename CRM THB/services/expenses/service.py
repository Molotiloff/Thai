from decimal import localcontext
from typing import cast

from domain.expenses import allocate_shares, settle_fifo
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
    canonical_json,
    decimal,
    exact,
    revision_details,
)
from domain.wallet_valuation import liability_change
from services.expenses.ports import ExpenseReadPort, ExpenseUnitOfWork


class ExpenseDocument:
    def __init__(self, save):
        self._save = save

    async def save(self, operation_id, work, plan):
        await self._save(operation_id, cast(ExpenseUnitOfWork, work), plan)


def month(period):
    if period.day != 1:
        raise InvalidPosting("Период должен быть первым числом месяца")
    return period


class ExpenseService:
    def __init__(self, posting, reversals, queries: ExpenseReadPort):
        self._posting, self._reversals, self._queries = posting, reversals, queries

    async def record(
        self,
        *,
        key,
        actor_id,
        business_date,
        effective_at,
        category_id,
        instrument,
        amount,
        quoted_rate,
        payer,
        shares,
        comment="",
        revision_id=None,
        profit_revision_id=None,
        deposit=False,
        correction_of=None,
    ):
        if deposit:
            raise InvalidPosting("Возвратные депозиты отложены до следующего релиза")
        accounts = await self._queries.accounts(instrument, payer)
        amount = exact(decimal(amount), accounts["precision"])
        if amount <= 0:
            raise InvalidPosting("Сумма расхода должна быть положительной")
        quote = FxQuote(
            accounts["currency_code"],
            decimal(quoted_rate),
            "units_per_usdt" if accounts["currency_code"] == "THB" else "usdt_per_unit",
            instrument,
        )
        value = quote.value(amount)
        if value <= 0:
            raise InvalidPosting("Оценка расхода меньше минимальной единицы USDT")
        allocated, inputs = allocate_shares(
            amount, value, accounts["precision"], shares, accounts["participants"]
        )
        with localcontext() as context:
            context.prec = 80
            business = sum(share.usdt for share in allocated if share.code != "daniyar")
        personal = next(share for share in allocated if share.code == "daniyar")
        ids = {accounts["operating_expense"], accounts["fx_income"], accounts["fx_loss"]}
        if payer == "business":
            ids |= {accounts["cash"], accounts["receivable"]}
        elif business:
            ids.add(accounts["funding"])
        mode = PostingMode.LEDGER if payer == "business" or business else PostingMode.MEMORANDUM
        payload = {
            "actor_id": actor_id,
            "category_id": category_id,
            "instrument": instrument,
            "amount": amount,
            "rate": quote.quoted_rate,
            "payer": payer,
            "shares": inputs,
            "comment": comment,
            "effective_at": effective_at,
            "expense_revision_id": revision_id,
            "correction_of": correction_of,
        }
        command = OperationCommand(
            key,
            "expense",
            actor_id,
            business_date,
            effective_at,
            tuple(sorted(ids)),
            canonical_json(revision_details(payload, profit_revision_id)),
            context=LedgerContext.EXPENSES,
            mode=mode,
        )

        async def prepare(work):
            await work.expenses.guard(actor_id, "expense.post")
            await work.expenses.category(category_id)
            if correction_of is not None:
                await work.expenses.correction(correction_of)

        def build(state):
            lines = []
            if business:
                lines.append(
                    JournalLine(
                        accounts["operating_expense"],
                        "USDT",
                        Side.DEBIT,
                        business,
                        business,
                        Valuation.BASE,
                    )
                )
            if payer == "business":
                if personal.amount:
                    lines.append(
                        JournalLine(
                            accounts["receivable"],
                            quote.currency,
                            Side.DEBIT,
                            personal.amount,
                            personal.usdt,
                            Valuation.CARRYING,
                        )
                    )
                cost = state.position(instrument).withdrawal_cost(amount)
                lines.append(
                    JournalLine(
                        accounts["cash"],
                        quote.currency,
                        Side.CREDIT,
                        amount,
                        cost,
                        Valuation.CARRYING,
                    )
                )
                return balance_with_fx(
                    tuple(lines),
                    income_account_id=accounts["fx_income"],
                    loss_account_id=accounts["fx_loss"],
                )
            if business:
                lines.append(
                    JournalLine(
                        accounts["funding"], "USDT", Side.CREDIT, business, business, Valuation.BASE
                    )
                )
            return PostingPlan(tuple(lines), mode)

        async def save(operation_id, work, plan):
            await work.expenses.save_expense(
                operation_id, payload, quote, allocated, inputs, accounts
            )

        return await self._posting.post(
            command, build, prepare=prepare, document=ExpenseDocument(save),valuation_quotes=(quote,) if payer=="business" else ()
        )

    async def settle(
        self,
        *,
        key,
        actor_id,
        business_date,
        effective_at,
        debtor,
        creditor,
        currency,
        amount,
        method="direct",
        instrument=None,
        quoted_rate=None,
        client_id=None,
        obligation_ids=None,
        revision_id=None,
        profit_revision_id=None,
    ):
        if method not in ("direct", "cash", "wallet", "netting"):
            raise InvalidPosting("Способ расчёта: direct, cash, wallet, netting")
        accounts = await self._queries.accounts(
            instrument
            or {"THB": "THB_CASH", "USD": "USD_BL", "EUR": "EUR", "USDT": "USDT", "RUB": "RUB"}.get(
                currency, ""
            ),
            debtor,
        )
        participants = accounts["participants"]
        if (
            creditor not in participants
            or creditor == debtor
            or accounts["currency_code"] != currency
        ):
            raise InvalidPosting("Нужны разные участники и инструмент той же валюты, что долг")
        precision = 6 if currency == "USDT" else 2
        amount = exact(decimal(amount), precision)
        if amount <= 0:
            raise InvalidPosting("Сумма расчёта должна быть положительной")
        if method in ("direct", "netting") and (
            instrument is not None or quoted_rate is not None or client_id is not None
        ):
            raise InvalidPosting("Прямой расчёт/зачёт не требует кассы, кошелька или курса")
        if method == "direct" and "business" in (debtor, creditor):
            raise InvalidPosting("С общей кассой рассчитываются методом cash или wallet")
        if method in ("cash", "wallet") and (instrument is None or quoted_rate is None):
            raise InvalidPosting("Для кассового/кошелькового расчёта укажите инструмент и курс")
        wallet = None
        if method == "wallet":
            if client_id is None and debtor=='daniyar':
                client_id=await self._queries.participant_client(debtor)
            if creditor != "business" or client_id is None:
                raise InvalidPosting(
                    "Удержание кошельком поддерживает только долг клиента общей кассе"
                )
            wallet = await self._queries.participant_wallet(
                client_id, participants[debtor], instrument
            )
        if method == "cash" and creditor not in ("business", "daniyar"):
            raise InvalidPosting(
                "Кассовое погашение поддерживает долг кассе или личное финансирование Данияром"
            )
        quote = (
            FxQuote(
                currency,
                decimal(quoted_rate),
                "units_per_usdt" if currency == "THB" else "usdt_per_unit",
                instrument,
            )
            if method in ("cash", "wallet")
            else None
        )
        ids = {accounts["fx_income"], accounts["fx_loss"], *accounts["owner_funding"].values()}
        # Native receivable and the non-owner's business payable are provisioned by migration.
        ids.add(accounts["receivable"])
        creditor_accounts = await self._queries.accounts(accounts["code"], "daniyar")
        ids.add(creditor_accounts["funding"])
        if method == "cash":
            ids.add(accounts["cash"])
        if wallet:
            ids.add(wallet["wallet"])
        # Direct / netting between owners is a memorandum. Dany as creditor clears a business payable.
        ledger = (
            method in ("cash", "wallet")
            or creditor == "daniyar"
            or (method == "netting" and debtor == "daniyar")
        )
        mode = PostingMode.LEDGER if ledger else PostingMode.MEMORANDUM
        if ledger and method in ("direct", "netting"):
            previous_mode = await self._queries.posting_mode(key)
            if previous_mode is not None:
                mode = PostingMode(previous_mode)
            else:
                preview = settle_fifo(
                    await self._queries.preview_debts(
                        participants[debtor],
                        participants[creditor],
                        currency,
                        business_date,
                        obligation_ids,
                    ),
                    amount,
                )
                if method == "netting":
                    preview += settle_fifo(
                        await self._queries.preview_debts(
                            participants[creditor], participants[debtor], currency, business_date
                        ),
                        amount,
                        "reverse",
                    )
                mode = (
                    PostingMode.LEDGER
                    if any(a.account_id is not None and a.usdt for a in preview)
                    else PostingMode.MEMORANDUM
                )
        payload = {
            "actor_id": actor_id,
            "effective_at": effective_at,
            "debtor": debtor,
            "creditor": creditor,
            "debtor_id": participants[debtor],
            "creditor_id": participants[creditor],
            "currency": currency,
            "amount": amount,
            "method": method,
            "instrument": instrument,
            "rate": quote.quoted_rate if quote else None,
            "client_id": client_id,
            "obligation_ids": sorted(obligation_ids) if obligation_ids else None,
            "wallet_account_id": wallet["wallet"] if wallet else None,
            "expense_revision_id": revision_id,
        }
        command = OperationCommand(
            key,
            "expense_netting" if method == "netting" else "expense_payment",
            actor_id,
            business_date,
            effective_at,
            tuple(sorted(ids)),
            canonical_json(revision_details(payload, profit_revision_id)),
            context=LedgerContext.EXPENSES,
            mode=mode,
        )
        prepared = {}

        async def prepare(work):
            await work.expenses.guard(actor_id, "expense.post")
            if wallet:
                await work.clients.lock_client(client_id)
            rows = await work.expenses.debts(
                participants[debtor],
                participants[creditor],
                currency,
                business_date,
                obligation_ids,
            )
            allocations = settle_fifo(rows, amount)
            if method == "netting":
                reverse_rows = await work.expenses.debts(
                    participants[creditor], participants[debtor], currency, business_date
                )
                allocations += settle_fifo(reverse_rows, amount, "reverse")
            prepared["allocations"] = allocations
            if (
                method == "cash"
                and creditor == "daniyar"
                and any(a.account_id is None for a in allocations)
            ):
                raise InvalidPosting(
                    "Нельзя выдать деньги общей кассы для личного долга без бизнес-обязательства"
                )

        def build(state):
            allocations = prepared["allocations"]
            lines = []
            for allocation in allocations:
                if allocation.account_id is None:
                    continue
                account = state.account(allocation.account_id)
                if account.purpose == "expense_receivable":
                    lines.append(
                        JournalLine(
                            account.id,
                            currency,
                            Side.CREDIT,
                            allocation.amount,
                            allocation.usdt,
                            Valuation.HISTORICAL,
                        )
                    )
                elif allocation.usdt:
                    lines.append(
                        JournalLine(
                            account.id,
                            "USDT",
                            Side.DEBIT,
                            allocation.usdt,
                            allocation.usdt,
                            Valuation.BASE,
                        )
                    )
                    if method in ("direct", "netting"):
                        owner = participants[
                            debtor if allocation.direction == "forward" else creditor
                        ]
                        if owner not in accounts["owner_funding"]:
                            raise InvalidPosting("Погашение финансирования требует собственника")
                        lines.append(
                            JournalLine(
                                accounts["owner_funding"][owner],
                                "USDT",
                                Side.CREDIT,
                                allocation.usdt,
                                allocation.usdt,
                                Valuation.BASE,
                            )
                        )
            if method == "cash":
                incoming = creditor == "business"
                cost = (
                    quote.value(amount)
                    if incoming
                    else state.position(instrument).withdrawal_cost(amount)
                )
                lines.append(
                    JournalLine(
                        accounts["cash"],
                        currency,
                        Side.DEBIT if incoming else Side.CREDIT,
                        amount,
                        cost,
                        Valuation.RATE if incoming else Valuation.CARRYING,
                        quote if incoming else None,
                    )
                )
            elif wallet:
                original = state.account(wallet["wallet"])
                cost = -liability_change(-original.native, -original.usdt, -amount, quote)
                lines.append(
                    JournalLine(original.id, currency, Side.DEBIT, amount, cost, Valuation.CARRYING)
                )
            if lines:
                return balance_with_fx(
                    tuple(lines),
                    income_account_id=accounts["fx_income"],
                    loss_account_id=accounts["fx_loss"],
                )
            return PostingPlan((), PostingMode.MEMORANDUM)

        async def save(operation_id, work, plan):
            await work.expenses.save_resolution(
                operation_id, payload, prepared["allocations"], quote
            )
            for source in sorted({a.source_operation_id for a in prepared["allocations"]}):
                await work.journal.add_dependency(source, operation_id)

        return await self._posting.post(
            command, build, prepare=prepare, document=ExpenseDocument(save),valuation_quotes=(quote,) if quote and method in ("cash","wallet") else ()
        )

    async def reverse(
        self, *, key, actor_id, operation_id, business_date, effective_at, reason, revision_id=None, profit_revision_id=None
    ):
        async def prepare(work):
            await work.expenses.guard(actor_id, "expense.post")
            await work.expenses.reversible(operation_id, business_date)

        return await self._reversals.reverse(
            original_id=operation_id,
            key=key,
            actor_id=actor_id,
            business_date=business_date,
            effective_at=effective_at,
            reason=reason,
            prepare=prepare,
            details=revision_details({"expense_revision_id": revision_id}, profit_revision_id),
        )

    async def close(self, *, key, actor_id, period, effective_at, revision_id=None, profit_revision_id=None):
        month(period)
        command = OperationCommand(
            key,
            "expense_close",
            actor_id,
            period,
            effective_at,
            (),
            canonical_json(revision_details({"period": period, "expense_revision_id": revision_id}, profit_revision_id)),
            context=LedgerContext.EXPENSES,
            mode=PostingMode.MEMORANDUM,
        )

        async def prepare(work):
            await work.expenses.guard(actor_id, "period.revise" if revision_id else "period.close")
            await work.expenses.prepare_close(period, revision_id)

        async def save(operation_id, work, plan):
            await work.expenses.save_close(operation_id, period, revision_id)

        return await self._posting.post(
            command,
            lambda state: PostingPlan((), PostingMode.MEMORANDUM),
            prepare=prepare,
            document=ExpenseDocument(save),
        )

    async def start_revision(self, *, key, actor_id, period, business_date, effective_at, reason, profit_revision_id=None):
        month(period)
        if not reason.strip():
            raise InvalidPosting("Причина новой ревизии обязательна")
        command = OperationCommand(
            key,
            "expense_revision",
            actor_id,
            business_date,
            effective_at,
            (),
            canonical_json(revision_details({"period": period, "reason": reason}, profit_revision_id)),
            context=LedgerContext.LEDGER,
            mode=PostingMode.MEMORANDUM,
        )

        async def prepare(work):
            await work.expenses.guard(actor_id, "period.revise")

        async def save(operation_id, work, plan):
            await work.expenses.start_revision(operation_id, period, reason)

        return await self._posting.post(
            command,
            lambda state: PostingPlan((), PostingMode.MEMORANDUM),
            prepare=prepare,
            document=ExpenseDocument(save),
        )
