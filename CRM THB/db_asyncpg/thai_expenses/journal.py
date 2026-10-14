import json

from db_asyncpg.thai_finance.repositories import JournalRepository
from domain.finance.models import InvalidPosting, LedgerContext


class ExpenseJournalRepository(JournalRepository):
    """Serialize debt mutations and validate scoped, owner-authorized revisions."""

    async def guard(self, command, permission):
        await self._connection.execute("SELECT pg_advisory_xact_lock(740810,7)")
        if command.kind == "expense_close":
            await self._connection.execute("SELECT pg_advisory_xact_lock(740810,3)")
        self._revision_id = json.loads(command.details_json).get("expense_revision_id")
        self._actor_id = command.actor_id
        await super().guard(command, permission)
        if self._revision_id and not await self._connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM financial_operations WHERE idempotency_key=$1)",
            command.key,
        ):
            await self._permission(command.actor_id, "period.revise")
            if not await self._revision_available(command.business_date):
                raise InvalidPosting("Ревизия не открыта для месяца этой операции")

    async def _period_closed(self, context, day):
        if not await super()._period_closed(context, day):
            return False
        if context != LedgerContext.EXPENSES or not self._revision_id:
            return True
        ledger_closed = await self._connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM period_locks WHERE context='ledger' AND period=date_trunc('month',$1::date)::date)",
            day,
        )
        if await self._profit_revision_available(day):
            ledger_closed = False
        permitted = await self._revision_available(day)
        if permitted and not ledger_closed:
            await self._permission(self._actor_id, "period.revise")
            return False
        return True

    async def _revision_available(self, day):
        return await self._connection.fetchval(
            """
            SELECT EXISTS(SELECT 1 FROM expense_period_revisions r JOIN active_financial_originals o ON o.id=r.operation_id
                WHERE r.id=$1 AND r.period=date_trunc('month',$2::date)::date
                    AND NOT EXISTS(SELECT 1 FROM expense_revision_completions c WHERE c.revision_id=r.id))
        """,
            self._revision_id,
            day,
        )
