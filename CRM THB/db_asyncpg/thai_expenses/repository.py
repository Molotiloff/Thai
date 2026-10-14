from db_asyncpg.thai_expenses.statements import STATEMENT_SQL
from domain.finance.models import InvalidPosting, ReversalBlocked


class ExpenseRepository:
    def __init__(self, connection):
        self._db = connection

    async def guard(self, actor_id, permission):
        from db_asyncpg.thai_finance.repositories import JournalRepository

        await JournalRepository(self._db)._permission(actor_id, permission)

    async def category(self, category_id):
        if not await self._db.fetchval(
            "SELECT EXISTS(SELECT 1 FROM expense_categories WHERE id=$1 AND is_active)", category_id
        ):
            raise InvalidPosting("Категория расхода не найдена")

    async def debts(self, debtor, creditor, currency, day, obligation_ids=None):
        rows = await self._db.fetch(
            """
            SELECT * FROM expense_obligation_balances WHERE debtor_party_id=$1 AND creditor_party_id=$2
                AND currency_code=$3 AND remaining>0 AND business_date<=$4
                AND ($5::bigint[] IS NULL OR id=ANY($5)) ORDER BY business_date,source_operation_id,id
        """,
            debtor,
            creditor,
            currency,
            day,
            obligation_ids,
        )
        return [dict(row) for row in rows]

    async def rate(self, quote, actor_id, effective_at):
        return await self._db.fetchval(
            """
            INSERT INTO fx_rate_snapshots(currency_code,instrument_code,quoted_direction,quoted_rate,usdt_per_unit,effective_at,created_by)
                VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
        """,
            quote.currency,
            quote.instrument,
            quote.direction,
            quote.quoted_rate,
            quote.usdt_per_unit,
            effective_at,
            actor_id,
        )

    async def save_expense(self, operation_id, command, quote, shares, inputs, accounts):
        rate_id = await self.rate(quote, command["actor_id"], command["effective_at"])
        cash = command["payer"] == "business"
        expense_id = await self._db.fetchval(
            """
            INSERT INTO expenses(operation_id,category_id,currency_code,amount,amount_usdt,rate_snapshot_id,payer_kind,payer_party_id,cash_instrument_code,comment)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id
        """,
            operation_id,
            command["category_id"],
            quote.currency,
            command["amount"],
            quote.value(command["amount"]),
            rate_id,
            "cash" if cash else "party",
            None if cash else accounts["participants"][command["payer"]],
            quote.instrument if cash else None,
            command["comment"],
        )
        for code, percent in inputs.items():
            await self._db.execute(
                "INSERT INTO expense_share_inputs VALUES ($1,$2,$3)", expense_id, code, percent
            )
        for share in shares:
            await self._db.execute(
                "INSERT INTO expense_allocations VALUES ($1,$2,$3,$4,$5)",
                expense_id,
                share.party_id,
                share.percent,
                share.amount,
                share.usdt,
            )
            if (
                not share.amount
                or (cash and share.code != "daniyar")
                or (not cash and share.code == command["payer"])
            ):
                continue
            account = (
                accounts["receivable"]
                if cash
                else accounts["funding"]
                if command["payer"] == "daniyar"
                else None
            )
            await self._db.execute(
                """
                INSERT INTO expense_obligations(expense_id,currency_code,debtor_party_id,creditor_party_id,amount,original_usdt,business_ledger_account_id)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
            """,
                expense_id,
                quote.currency,
                share.party_id,
                accounts["participants"][command["payer"]],
                share.amount,
                share.usdt,
                account,
            )
        if command.get("correction_of") is not None:
            await self._db.execute(
                "INSERT INTO expense_corrections VALUES ($1,$2,$3)",
                command["correction_of"],
                expense_id,
                operation_id,
            )
        return expense_id

    async def save_resolution(self, operation_id, command, allocations, quote):
        rate_id = (
            await self.rate(quote, command["actor_id"], command["effective_at"]) if quote else None
        )
        resolution_id = await self._db.fetchval(
            """
            INSERT INTO expense_resolutions(operation_id,kind,currency_code,amount,payer_party_id,recipient_party_id,method,payment_instrument_code,payment_quantity,payment_rate_snapshot_id,wallet_account_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING id
        """,
            operation_id,
            "netting" if command["method"] == "netting" else "payment",
            command["currency"],
            command["amount"],
            command["debtor_id"],
            command["creditor_id"],
            command["method"],
            quote.instrument if quote else None,
            command["amount"] if quote else None,
            rate_id,
            command.get("wallet_account_id"),
        )
        for allocation in allocations:
            await self._db.execute(
                "INSERT INTO expense_resolution_allocations VALUES ($1,$2,$3,$4,$5)",
                resolution_id,
                allocation.obligation_id,
                allocation.direction,
                allocation.amount,
                allocation.usdt,
            )
        return resolution_id

    async def prepare_close(self, period, revision_id):
        latest = await self._db.fetchrow(
            "SELECT * FROM expense_period_closures WHERE period=$1 ORDER BY revision DESC LIMIT 1",
            period,
        )
        if revision_id is None:
            if latest is not None:
                raise InvalidPosting("Месяц уже закрыт; нужна новая ревизия")
        else:
            if latest is None or not await self._db.fetchval(
                """
                SELECT EXISTS(SELECT 1 FROM expense_period_revisions WHERE id=$1 AND period=$2 AND supersedes_id=$3
                    AND NOT EXISTS(SELECT 1 FROM expense_revision_completions WHERE revision_id=$1))
            """,
                revision_id,
                period,
                latest["id"],
            ):
                raise InvalidPosting("Открытая ревизия месяца не найдена")
        return dict(latest) if latest else {}

    async def save_close(self, operation_id, period, revision_id):
        latest = await self.prepare_close(period, revision_id)
        closure_id = await self._db.fetchval(
            """
            INSERT INTO expense_period_closures(period,revision,operation_id,supersedes_id) VALUES ($1,$2,$3,$4) RETURNING id
        """,
            period,
            latest.get("revision", 0) + 1,
            operation_id,
            latest.get("id"),
        )
        rows = await self._db.fetch(STATEMENT_SQL, period)
        for row in rows:
            await self._db.execute(
                """
                INSERT INTO expense_statement_rows(closure_id,debtor_party_id,creditor_party_id,currency_code,opening,accrued,settled,netted,corrected,closing)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            """,
                closure_id,
                *tuple(row),
            )
        if revision_id:
            await self._db.execute(
                "INSERT INTO expense_revision_completions VALUES ($1,$2)", revision_id, operation_id
            )
        else:
            await self._db.execute(
                "INSERT INTO period_locks(context,period,closed_by_operation_id) VALUES ('expenses',$1,$2)",
                period,
                operation_id,
            )
        return closure_id

    async def start_revision(self, operation_id, period, reason):
        latest = await self._db.fetchrow(
            "SELECT id FROM expense_period_closures WHERE period=$1 ORDER BY revision DESC LIMIT 1",
            period,
        )
        if latest is None:
            raise InvalidPosting("Можно пересмотреть только закрытый месяц")
        return await self._db.fetchval(
            "INSERT INTO expense_period_revisions(period,operation_id,supersedes_id,reason) VALUES ($1,$2,$3,$4) RETURNING id",
            period,
            operation_id,
            latest["id"],
            reason,
        )

    async def correction(self, expense_id):
        if not await self._db.fetchval(
            """
            SELECT EXISTS(SELECT 1 FROM expenses e WHERE e.id=$1
                AND NOT EXISTS(SELECT 1 FROM active_financial_originals o WHERE o.id=e.operation_id)
                AND NOT EXISTS(SELECT 1 FROM expense_corrections c WHERE c.original_expense_id=e.id))
        """,
            expense_id,
        ):
            raise InvalidPosting("Коррекция должна ссылаться на отменённый расход без замены")

    async def reversible(self, operation_id, day):
        original_day = await self._db.fetchval(
            "SELECT business_date FROM financial_operations WHERE id=$1", operation_id
        )
        if original_day is not None and day < original_day:
            raise InvalidPosting("Дата сторно не может предшествовать исходной операции")
        if not await self._db.fetchval(
            "SELECT EXISTS(SELECT 1 FROM expenses WHERE operation_id=$1 UNION ALL SELECT 1 FROM expense_resolutions WHERE operation_id=$1)",
            operation_id,
        ):
            raise ReversalBlocked("Через сервис расходов можно отменять только расход или расчёт")
