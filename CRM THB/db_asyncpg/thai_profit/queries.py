from db_asyncpg.thai_profit.repository import ProfitRepository
from domain.accounting_policy import summarize
from domain.finance.models import InvalidPosting


class ProfitQueries:
    def __init__(self, pool):
        self._pool = pool

    async def accounts(self):
        rows = await self._pool.fetch(
            "SELECT id,purpose,party_id FROM ledger_accounts WHERE purpose IN ('trading_income','trading_loss','platform_commission_expense','platform_commission_payable','fx_income','fx_loss','retained_result','owner_profit_payable')"
        )
        result = {"owners": {}}
        participants = {
            row["code"]: row["party_id"]
            for row in await self._pool.fetch("SELECT * FROM business_participants")
        }
        result["participants"] = participants
        for row in rows:
            if row["purpose"] == "owner_profit_payable":
                result["owners"][row["party_id"]] = row["id"]
            else:
                result[row["purpose"]] = row["id"]
            if row["purpose"] == "platform_commission_payable":
                result["platform_party_id"] = row["party_id"]
        return result

    async def instrument(self, instrument):
        row = await self._pool.fetchrow(
            "SELECT i.*,c.ledger_account_id AS cash FROM instruments i JOIN cash_accounts c ON c.instrument_code=i.code WHERE i.code=$1 AND i.is_active",
            instrument,
        )
        if row is None:
            raise InvalidPosting("Инструмент выплаты не найден")
        return dict(row)

    async def bindings(self):
        async with self._pool.acquire() as connection:
            return await ProfitRepository(connection).bindings()

    async def optional_bindings(self):
        if not await self._pool.fetchval("SELECT EXISTS(SELECT 1 FROM business_client_bindings)"):
            return None
        return await self.bindings()

    async def pnl_accounts(self):
        return [
            dict(row)
            for row in await self._pool.fetch(
                "SELECT id,purpose FROM ledger_accounts WHERE account_class IN ('income','expense') ORDER BY id"
            )
        ]

    async def closure_mode(self, key, period):
        previous = await self._pool.fetchval(
            "SELECT posting_mode FROM financial_operations WHERE idempotency_key=$1 AND status='posted'",
            key,
        )
        if previous is not None:
            return previous
        async with (
            self._pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read", readonly=True),
        ):
            from decimal import Decimal, localcontext

            with localcontext() as context:
                context.prec = 80
                totals = {}
                repo = ProfitRepository(connection)
                for row in await repo.sources(period):
                    totals[row["account_id"]] = (
                        totals.get(row["account_id"], Decimal(0)) + row["credit_signed_usdt"]
                    )
                transferred = sum(
                    row["daniyar_share_usdt"] for row in await repo.transferable(period)
                )
                return "ledger" if any(totals.values()) or transferred else "memorandum"

    async def report(self, period):
        async with (
            self._pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read", readonly=True),
        ):
            repo = ProfitRepository(connection)
            sources = await repo.sources(period)
            term = await repo.term(period)
            closure = await connection.fetchrow(
                "SELECT c.*,EXISTS(SELECT 1 FROM active_financial_originals o WHERE o.id=c.operation_id) AS active FROM profit_period_closures c WHERE period=$1 ORDER BY revision DESC LIMIT 1",
                period,
            )
            totals = summarize(sources)
            changed = bool(
                closure
                and (
                    not closure["active"]
                    or closure["share_term_id"] != term["id"]
                    or any(closure[field] != value for field, value in totals.items())
                )
            )
            return {
                "period": period,
                **totals,
                "needs_revision": changed,
                "term": term,
                "sources": sources,
                "closure": dict(closure) if closure else None,
            }

    async def closure(self, period, revision=None):
        async with (
            self._pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read", readonly=True),
        ):
            row = await connection.fetchrow(
                "SELECT * FROM profit_period_closures WHERE period=$1 AND ($2::int IS NULL OR revision=$2) ORDER BY revision DESC LIMIT 1",
                period,
                revision,
            )
            if row is None:
                raise InvalidPosting("Закрытие прибыли не найдено")
            result = dict(row)
            result["term"] = dict(
                await connection.fetchrow(
                    "SELECT * FROM profit_share_terms WHERE id=$1", row["share_term_id"]
                )
            )
            result["sources"] = [
                dict(r)
                for r in await connection.fetch(
                    """
                    SELECT s.*,a.purpose,o.business_date FROM profit_closure_sources s
                        JOIN ledger_accounts a ON a.id=s.account_id
                        JOIN financial_operations o ON o.id=s.source_operation_id
                    WHERE s.closure_id=$1 ORDER BY s.source_operation_id,s.account_id
                    """,
                    row["id"],
                )
            ]
            result["accruals"] = [
                dict(r)
                for r in await connection.fetch(
                    "SELECT * FROM partner_profit_accruals WHERE closure_id=$1 ORDER BY owner_party_id",
                    row["id"],
                )
            ]
            result["best_change_transfer"] = [
                dict(r)
                for r in await connection.fetch(
                    "SELECT * FROM best_change_period_transfers WHERE operation_id=$1",
                    row["operation_id"],
                )
            ]
            return result

    async def terms(self):
        return [
            dict(row)
            for row in await self._pool.fetch(
                "SELECT * FROM profit_share_terms ORDER BY valid_from,id"
            )
        ]

    async def obligations(self):
        async with (
            self._pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read", readonly=True),
        ):
            return {
                "owners": [
                    dict(row)
                    for row in await connection.fetch(
                        "SELECT * FROM owner_profit_balances ORDER BY period,id"
                    )
                ],
                "platform": [
                    dict(row)
                    for row in await connection.fetch(
                        "SELECT * FROM platform_commission_balances ORDER BY business_date,id"
                    )
                ],
            }

    async def best_change(self, before_id=None, limit=50):
        rows = await self._pool.fetch(
            """
            SELECT c.*,d.operation_id,d.client_id,d.attributes,o.business_date,
                EXISTS(SELECT 1 FROM active_financial_originals a WHERE a.id=d.operation_id) AS active
            FROM best_change_calculations c JOIN deals d ON d.id=c.deal_id JOIN financial_operations o ON o.id=d.operation_id
            WHERE ($1::bigint IS NULL OR c.deal_id<$1) ORDER BY c.deal_id DESC LIMIT $2
        """,
            before_id,
            limit,
        )
        import json

        return [{**dict(row), "attributes": json.loads(row["attributes"])} for row in rows]

    async def best_change_report(self, period):
        async with (
            self._pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read", readonly=True),
        ):
            row = await connection.fetchrow(
                """
                SELECT count(*) AS count,coalesce(sum(c.platform_fee_usdt),0) AS fee_usdt,
                    coalesce(sum(c.daniyar_share_usdt),0) AS daniyar_usdt,coalesce(sum(c.business_share_usdt),0) AS business_usdt
                FROM best_change_calculations c JOIN deals d ON d.id=c.deal_id JOIN active_financial_originals o ON o.id=d.operation_id
                WHERE o.business_date>=$1 AND o.business_date<($1::date+interval '1 month')
            """,
                period,
            )
            result = dict(row)
            result["period"] = period
            result["transfers"] = [
                dict(r)
                for r in await connection.fetch(
                    "SELECT t.* FROM best_change_period_transfers t JOIN active_financial_originals o ON o.id=t.operation_id WHERE t.period=$1",
                    period,
                )
            ]
            bindings = await ProfitRepository(connection).bindings()
            result["buffer"] = dict(
                await connection.fetchrow(
                    "SELECT * FROM posted_account_balances WHERE account_id=$1",
                    bindings["best_change_buffer"]["ledger_account_id"],
                )
            )
            return result

    async def revisions(self, period):
        return [
            dict(row)
            for row in await self._pool.fetch(
                "SELECT r.*,c.operation_id AS completed_by_operation_id FROM profit_period_revisions r LEFT JOIN profit_revision_completions c ON c.revision_id=r.id WHERE r.period=$1 ORDER BY r.id",
                period,
            )
        ]


    async def best_change_context(self, deal_id):
        async with self._pool.acquire() as db,db.transaction(isolation='repeatable_read',readonly=True):
            return await ProfitRepository(db).best_change_context(deal_id)

    async def best_change_by_reference(self, reference):
        return await self._pool.fetchval("SELECT id FROM deals WHERE external_reference=$1 AND kind='best_change'",reference)

    async def best_change_by_message(self, chat_id,message_id):
        return await self._pool.fetchval("SELECT deal_id FROM telegram_message_links WHERE chat_id=$1 AND message_id=$2 AND purpose='best_change'",chat_id,message_id)
