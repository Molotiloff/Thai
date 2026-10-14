from db_asyncpg.thai_expenses.statements import STATEMENT_SQL
from domain.finance.models import InvalidPosting


class ExpenseQueries:
    def __init__(self, pool):
        self._pool = pool

    async def accounts(self, instrument, payer):
        participants = {
            row["code"]: row["party_id"]
            for row in await self._pool.fetch("SELECT * FROM business_participants")
        }
        if payer not in participants:
            raise InvalidPosting("Плательщик: Данияр, ПЕ, SkyEX или общая касса")
        instrument_row = await self._pool.fetchrow(
            "SELECT i.*,c.ledger_account_id AS cash FROM instruments i JOIN cash_accounts c ON c.instrument_code=i.code WHERE i.code=$1 AND i.is_active",
            instrument,
        )
        if instrument_row is None:
            raise InvalidPosting("Неизвестный инструмент расхода")
        result = {**dict(instrument_row), "participants": participants}
        for row in await self._pool.fetch(
            "SELECT id,purpose,party_id,currency_code FROM ledger_accounts WHERE purpose IN ('operating_expense','personal_expense_funding','personal_expense_payable','expense_receivable','fx_income','fx_loss')"
        ):
            if row["purpose"] in ("operating_expense", "fx_income", "fx_loss"):
                result[row["purpose"]] = row["id"]
            elif (
                row["purpose"] == "expense_receivable"
                and row["currency_code"] == result["currency_code"]
            ):
                result["receivable"] = row["id"]
            elif row["party_id"] == participants[payer] and row["purpose"] in (
                "personal_expense_funding",
                "personal_expense_payable",
            ):
                result["funding"] = row["id"]
            if row["purpose"] == "personal_expense_funding":
                result.setdefault("owner_funding", {})[row["party_id"]] = row["id"]
        return result

    async def posting_mode(self, key):
        return await self._pool.fetchval(
            "SELECT posting_mode FROM financial_operations WHERE idempotency_key=$1 AND status='posted'",
            key,
        )

    async def preview_debts(self, debtor, creditor, currency, day, obligation_ids=None):
        from db_asyncpg.thai_expenses.repository import ExpenseRepository

        async with self._pool.acquire() as connection:
            return await ExpenseRepository(connection).debts(
                debtor, creditor, currency, day, obligation_ids
            )

    async def participant_wallet(self, client_id, debtor_id, instrument):
        from db_asyncpg.thai_clients.queries import ClientQueries

        party = await self._pool.fetchval("SELECT party_id FROM clients WHERE id=$1", client_id)
        binding = await self._pool.fetchval(
            """
            SELECT EXISTS(SELECT 1 FROM business_client_bindings b JOIN business_participants p ON p.code='daniyar'
                WHERE b.purpose='daniyar_balance' AND b.client_id=$1 AND p.party_id=$2)
        """,
            client_id,
            debtor_id,
        )
        if party != debtor_id and not binding:
            raise InvalidPosting("Для удержания нужен кошелёк именно должника")
        return await ClientQueries(self._pool).wallet_accounts(client_id, instrument)

    async def participant_client(self, code):
        if code!='daniyar':
            return None
        return await self._pool.fetchval("""
            SELECT b.client_id FROM business_client_bindings b JOIN clients c ON c.id=b.client_id
                WHERE b.purpose='daniyar_balance' AND c.is_active
        """)

    async def categories(self):
        return [
            dict(row)
            for row in await self._pool.fetch("SELECT * FROM expense_categories ORDER BY id")
        ]

    async def participants(self):
        return [
            dict(row)
            for row in await self._pool.fetch(
                "SELECT b.*,p.name FROM business_participants b JOIN parties p ON p.id=b.party_id ORDER BY b.code"
            )
        ]

    async def detail(self, expense_id):
        row = await self._pool.fetchrow(
            """
            SELECT e.*,o.business_date,o.created_by,o.effective_at,r.quoted_rate,r.usdt_per_unit,
                NOT EXISTS(SELECT 1 FROM active_financial_originals a WHERE a.id=e.operation_id) AS cancelled,
                (SELECT coalesce(sum(a.amount),0) FROM expense_allocations a JOIN business_participants b ON b.party_id=a.party_id WHERE a.expense_id=e.id AND b.code IN ('pe','skyex')) AS business_amount,
                (SELECT coalesce(sum(a.amount_usdt),0) FROM expense_allocations a JOIN business_participants b ON b.party_id=a.party_id WHERE a.expense_id=e.id AND b.code IN ('pe','skyex')) AS business_usdt
            FROM expenses e JOIN financial_operations o ON o.id=e.operation_id JOIN fx_rate_snapshots r ON r.id=e.rate_snapshot_id WHERE e.id=$1
        """,
            expense_id,
        )
        if row is None:
            raise InvalidPosting("Расход не найден")
        result = dict(row)
        for key, table in (
            ("inputs", "expense_share_inputs"),
            ("allocations", "expense_allocations"),
            ("obligations", "expense_obligations"),
        ):
            result[key] = [
                dict(row)
                for row in await self._pool.fetch(
                    f"SELECT * FROM {table} WHERE expense_id=$1", expense_id
                )
            ]
        result["corrections"] = [
            dict(row)
            for row in await self._pool.fetch(
                "SELECT * FROM expense_corrections WHERE original_expense_id=$1 OR replacement_expense_id=$1",
                expense_id,
            )
        ]
        return result

    async def list(self, before_id=None, limit=50, period=None, participant_id=None, currency=None):
        rows = await self._pool.fetch(
            """
            SELECT e.*,o.business_date,c.name AS category_name,
                EXISTS(SELECT 1 FROM active_financial_originals a WHERE a.id=e.operation_id) AS active,
                (SELECT coalesce(sum(a.amount_usdt),0) FROM expense_allocations a JOIN business_participants b ON b.party_id=a.party_id WHERE a.expense_id=e.id AND b.code IN ('pe','skyex')) AS business_usdt
            FROM expenses e JOIN financial_operations o ON o.id=e.operation_id JOIN expense_categories c ON c.id=e.category_id
            WHERE ($1::bigint IS NULL OR e.id<$1)
                AND ($3::date IS NULL OR (o.business_date>=$3 AND o.business_date<($3::date+interval '1 month')))
                AND ($4::bigint IS NULL OR e.payer_party_id=$4 OR EXISTS(SELECT 1 FROM expense_allocations a WHERE a.expense_id=e.id AND a.party_id=$4))
                AND ($5::text IS NULL OR e.currency_code=$5) ORDER BY e.id DESC LIMIT $2
        """,
            before_id,
            limit,
            period,
            participant_id,
            currency,
        )
        return [dict(row) for row in rows]

    async def debts(self, participant_id=None, currency=None):
        async with (
            self._pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read", readonly=True),
        ):
            obligations = await connection.fetch(
                """
                SELECT b.*,d.name AS debtor_name,c.name AS creditor_name FROM expense_obligation_balances b
                    JOIN parties d ON d.id=b.debtor_party_id JOIN parties c ON c.id=b.creditor_party_id
                    WHERE remaining>0 AND ($1::bigint IS NULL OR $1 IN (debtor_party_id,creditor_party_id))
                        AND ($2::text IS NULL OR currency_code=$2) ORDER BY business_date,id
            """,
                participant_id,
                currency,
            )
            totals = await connection.fetch(
                """
                SELECT debtor_party_id,creditor_party_id,currency_code,sum(remaining) AS amount,sum(remaining_usdt) AS historical_usdt
                FROM expense_obligation_balances WHERE remaining>0 AND ($1::bigint IS NULL OR $1 IN (debtor_party_id,creditor_party_id))
                    AND ($2::text IS NULL OR currency_code=$2) GROUP BY debtor_party_id,creditor_party_id,currency_code
                ORDER BY debtor_party_id,creditor_party_id,currency_code
            """,
                participant_id,
                currency,
            )
            by_participant = await connection.fetch(
                """
                SELECT t.party_id,p.name,t.currency_code,sum(t.payable) AS payable,sum(t.receivable) AS receivable,
                    sum(t.payable-t.receivable) AS net_payable FROM (
                        SELECT debtor_party_id AS party_id,currency_code,remaining AS payable,0 AS receivable FROM expense_obligation_balances WHERE remaining>0
                        UNION ALL
                        SELECT creditor_party_id,currency_code,0,remaining FROM expense_obligation_balances WHERE remaining>0
                    ) t JOIN parties p ON p.id=t.party_id
                WHERE ($1::bigint IS NULL OR t.party_id=$1) AND ($2::text IS NULL OR t.currency_code=$2)
                GROUP BY t.party_id,p.name,t.currency_code ORDER BY t.party_id,t.currency_code
            """,
                participant_id,
                currency,
            )
            return {
                "obligations": [dict(row) for row in obligations],
                "totals": [dict(row) for row in totals],
                "by_participant": [dict(row) for row in by_participant],
            }

    async def resolution(self, resolution_id):
        row = await self._pool.fetchrow(
            "SELECT * FROM expense_resolutions WHERE id=$1", resolution_id
        )
        if row is None:
            raise InvalidPosting("Расчёт не найден")
        result = dict(row)
        result["allocations"] = [
            dict(row)
            for row in await self._pool.fetch(
                "SELECT * FROM expense_resolution_allocations WHERE resolution_id=$1", resolution_id
            )
        ]
        return result

    async def statement(self, period, revision=None):
        async with (
            self._pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read", readonly=True),
        ):
            closure = await connection.fetchrow(
                "SELECT * FROM expense_period_closures WHERE period=$1 AND ($2::int IS NULL OR revision=$2) ORDER BY revision DESC LIMIT 1",
                period,
                revision,
            )
            if closure is None:
                if revision is not None:
                    raise InvalidPosting("Ревизия выписки не найдена")
                return {
                    "period": period,
                    "closed": False,
                    "rows": [dict(row) for row in await connection.fetch(STATEMENT_SQL, period)],
                }
            rows = await connection.fetch(
                "SELECT * FROM expense_statement_rows WHERE closure_id=$1 ORDER BY debtor_party_id,creditor_party_id,currency_code",
                closure["id"],
            )
            stale = await connection.fetchval(
                """
                SELECT EXISTS(SELECT 1 FROM expense_period_closures x WHERE x.period<$1 AND x.operation_id>$2)
            """,
                period,
                closure["operation_id"],
            )
            return {
                **dict(closure),
                "closed": True,
                "needs_revision": stale,
                "rows": [dict(row) for row in rows],
            }

    async def revisions(self, period):
        rows = await self._pool.fetch(
            """
            SELECT r.*,c.operation_id AS completed_by_operation_id FROM expense_period_revisions r
                LEFT JOIN expense_revision_completions c ON c.revision_id=r.id WHERE r.period=$1 ORDER BY r.id
        """,
            period,
        )
        return [dict(row) for row in rows]
