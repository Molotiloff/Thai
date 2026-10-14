import asyncpg

from domain.finance.models import InvalidPosting


class CapitalQueries:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def accounts(self, agreement_id, instrument):
        row = await self._pool.fetchrow("""
            SELECT i.currency_code AS currency,i.precision,c.ledger_account_id AS cash,
                p.ledger_account_id AS principal,a.ledger_account_id AS payable
            FROM instruments i JOIN cash_accounts c ON c.instrument_code=i.code
            JOIN capital_principal_accounts p ON p.currency_code=i.currency_code AND p.agreement_id=$1
            JOIN capital_interest_accounts a ON a.agreement_id=$1 WHERE i.code=$2 AND i.is_active
        """, agreement_id, instrument)
        if row is None:
            raise InvalidPosting('Договор или инструмент не найден')
        accounts = dict(row)
        for account in await self._pool.fetch("SELECT id,purpose FROM ledger_accounts WHERE purpose IN ('fx_income','fx_loss','investor_interest_expense')"):
            accounts['interest_expense' if account['purpose'] == 'investor_interest_expense' else account['purpose']] = account['id']
        return accounts

    async def agreements(self, investor_id=None):
        rows = await self._pool.fetch("""
            SELECT a.*,p.name,t.monthly_interest_percent,
                coalesce((SELECT sum(remaining_principal) FROM capital_lot_balances b WHERE b.agreement_id=a.id),0) AS principal_usdt,
                coalesce((SELECT sum(c.principal_usdt) FROM capital_contributions c JOIN active_financial_originals o ON o.id=c.operation_id WHERE c.agreement_id=a.id),0) AS contributed_usdt,
                coalesce((SELECT sum(w.principal_usdt) FROM capital_withdrawals w JOIN active_financial_originals o ON o.id=w.operation_id WHERE w.agreement_id=a.id),0) AS withdrawn_usdt,
                coalesce((SELECT sum(final_usdt) FROM investor_interest_balances b WHERE b.agreement_id=a.id),0) AS accrued_usdt,
                coalesce((SELECT sum(paid_usdt) FROM investor_interest_balances b WHERE b.agreement_id=a.id),0) AS paid_usdt,
                coalesce((SELECT sum(remaining_usdt) FROM investor_interest_balances b WHERE b.agreement_id=a.id),0) AS interest_due_usdt
            FROM capital_agreements a JOIN parties p ON p.id=a.investor_party_id
            LEFT JOIN LATERAL (SELECT monthly_interest_percent FROM capital_interest_terms
                WHERE agreement_id=a.id AND valid_from<=(now() AT TIME ZONE 'Asia/Bangkok')::date
                    AND (valid_until IS NULL OR valid_until>(now() AT TIME ZONE 'Asia/Bangkok')::date)
                ORDER BY valid_from DESC LIMIT 1) t ON TRUE
            WHERE ($1::bigint IS NULL OR a.investor_party_id=$1) ORDER BY a.id
        """, investor_id)
        return [dict(r) for r in rows]

    async def investors(self):
        return [dict(r) for r in await self._pool.fetch("""
            SELECT p.*,
                coalesce((SELECT sum(b.remaining_principal) FROM capital_lot_balances b
                    JOIN capital_agreements a ON a.id=b.agreement_id WHERE a.investor_party_id=p.id),0) AS principal_usdt,
                coalesce((SELECT sum(b.final_usdt) FROM investor_interest_balances b
                    JOIN capital_agreements a ON a.id=b.agreement_id WHERE a.investor_party_id=p.id),0) AS accrued_usdt,
                coalesce((SELECT sum(b.paid_usdt) FROM investor_interest_balances b
                    JOIN capital_agreements a ON a.id=b.agreement_id WHERE a.investor_party_id=p.id),0) AS paid_usdt,
                coalesce((SELECT sum(b.remaining_usdt) FROM investor_interest_balances b
                    JOIN capital_agreements a ON a.id=b.agreement_id WHERE a.investor_party_id=p.id),0) AS interest_due_usdt
            FROM parties p JOIN party_roles r ON r.party_id=p.id WHERE r.role='investor' ORDER BY p.id
        """)]

    async def detail(self, agreement_id):
        agreements = await self.agreements()
        agreement = next((a for a in agreements if a['id'] == agreement_id), None)
        if agreement is None:
            raise InvalidPosting('Договор не найден')
        async with self._pool.acquire() as db:
            agreement['terms'] = [dict(r) for r in await db.fetch('SELECT * FROM capital_interest_terms WHERE agreement_id=$1 ORDER BY valid_from', agreement_id)]
            agreement['lots'] = [dict(r) for r in await db.fetch('SELECT * FROM capital_lot_balances WHERE agreement_id=$1 ORDER BY business_date,effective_at,operation_id,id', agreement_id)]
            agreement['accruals'] = [dict(r) for r in await db.fetch('SELECT * FROM investor_interest_balances WHERE agreement_id=$1 ORDER BY period,id', agreement_id)]
            agreement['lot_history'] = [dict(r) for r in await db.fetch("""
                SELECT c.*,o.business_date,o.effective_at,b.remaining_quantity,b.remaining_principal,
                    f.quoted_rate,f.quoted_direction,
                    b.id IS NOT NULL AS active FROM capital_contributions c
                JOIN financial_operations o ON o.id=c.operation_id
                JOIN fx_rate_snapshots f ON f.id=c.rate_snapshot_id
                LEFT JOIN capital_lot_balances b ON b.id=c.id WHERE c.agreement_id=$1 AND o.status='posted'
                ORDER BY o.business_date,o.effective_at,o.id,c.id
            """, agreement_id)]
            agreement['withdrawal_allocations'] = [dict(r) for r in await db.fetch("""
                SELECT x.*,w.operation_id,w.payout_instrument_code,o.business_date,
                    live.id IS NOT NULL AS active FROM capital_withdrawal_allocations x
                JOIN capital_withdrawals w ON w.id=x.withdrawal_id
                JOIN financial_operations o ON o.id=w.operation_id
                LEFT JOIN active_financial_originals live ON live.id=o.id
                WHERE x.agreement_id=$1 AND o.status='posted' ORDER BY o.id,x.contribution_id
            """, agreement_id)]
        return agreement

    async def history(self, agreement_id, *, before_id=None, limit=50, investor_id=None):
        rows = await self._pool.fetch("""
            WITH selected AS (SELECT id,created_operation_id FROM capital_agreements
                WHERE ($1::bigint IS NULL OR id=$1) AND ($4::bigint IS NULL OR investor_party_id=$4))
            SELECT o.id,o.kind,o.business_date,o.created_by,o.reversal_of_id,
                o.idempotency_key,o.command_details,o.reason,
                NOT EXISTS(SELECT 1 FROM financial_operations r WHERE r.reversal_of_id=o.id AND r.status='posted') AS active
            FROM financial_operations o WHERE o.status='posted' AND ($2::bigint IS NULL OR o.id<$2)
                AND (o.command_details->>'agreement' IN (SELECT id::text FROM selected) OR o.id IN (
                    SELECT created_operation_id FROM selected
                    UNION ALL SELECT change_operation_id FROM capital_interest_terms WHERE agreement_id IN (SELECT id FROM selected))
                    OR o.reversal_of_id IN (SELECT id FROM financial_operations
                        WHERE command_details->>'agreement' IN (SELECT id::text FROM selected)))
            ORDER BY o.id DESC LIMIT $3
        """, agreement_id, before_id, limit, investor_id)
        from db_asyncpg.thai_finance.queries import operation_dict
        return [operation_dict(r) for r in rows]
