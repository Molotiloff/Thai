from __future__ import annotations

from decimal import Decimal
from typing import Any

from db_asyncpg.repositories.base import BaseRepo


class ActCounterRepo(BaseRepo):
    async def service_amount(self, request_chat_id: int) -> Decimal:
        # Read the immutable journal, including the inverse lines of reversals.
        # Wallet swaps receive USDT with a debit and pay USDT with a credit.
        # Cash movements, loans, capital and BestChange never enter this counter.
        async with self._connection() as con:
            return await con.fetchval("""
                SELECT coalesce((SELECT sum(CASE l.side WHEN 'debit' THEN l.native_amount
                        ELSE -l.native_amount END)
                    FROM journal_lines l JOIN financial_operations o ON o.id=l.operation_id
                    LEFT JOIN financial_operations original ON original.id=o.reversal_of_id
                    JOIN client_wallet_accounts w ON w.ledger_account_id=l.account_id
                    WHERE o.status='posted' AND w.kind='client' AND w.instrument_code='USDT'
                        AND (o.kind='fx_trade' OR original.kind='fx_trade')),0)
                    + coalesce((SELECT sum(delta) FROM act_counter_adjustments
                        WHERE request_chat_id=$1),0)
            """, request_chat_id)

    async def reconcile_service_account(self, *, request_chat_id, amount, actor_id, key):
        async with self._connection() as con:
            await con.execute("SELECT pg_advisory_xact_lock(hashtextextended('act:'||$1::bigint::text,0))", request_chat_id)
            previous = await self.service_amount(request_chat_id)
            delta = amount - previous
            await con.execute("""
                INSERT INTO act_counter_adjustments(event_key,request_chat_id,actor_id,delta,target_amount)
                VALUES ($1,$2,$3,$4,$5)
            """, key, request_chat_id, actor_id, delta, amount)
            return {'previous_amount': str(previous), 'amount': str(amount), 'delta': str(delta)}

    async def link_act_request_transaction(
        self,
        *,
        req_id: str,
        request_chat_id: int,
        request_message_id: int,
        transaction_id: int,
        direction: str,
        table_req_id: str | None = None,
        status: str = "ACTIVE",
    ) -> int:
        direction_norm = str(direction).strip().upper()
        status_norm = str(status).strip().upper()

        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """
                    INSERT INTO act_request_transactions (
                        req_id,
                        table_req_id,
                        request_chat_id,
                        request_message_id,
                        transaction_id,
                        direction,
                        status
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (transaction_id) DO UPDATE SET
                        req_id = EXCLUDED.req_id,
                        table_req_id = COALESCE(EXCLUDED.table_req_id, act_request_transactions.table_req_id),
                        request_chat_id = EXCLUDED.request_chat_id,
                        request_message_id = EXCLUDED.request_message_id,
                        direction = EXCLUDED.direction,
                        status = EXCLUDED.status,
                        canceled_at = CASE
                            WHEN EXCLUDED.status = 'CANCELED' THEN now()
                            ELSE NULL
                        END
                    RETURNING id
                    """,
                    str(req_id),
                    str(table_req_id) if table_req_id is not None else None,
                    int(request_chat_id),
                    int(request_message_id),
                    int(transaction_id),
                    direction_norm,
                    status_norm,
                )
                return int(row["id"])

    async def cancel_act_request_transactions(self, *, req_id: str) -> int:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE act_request_transactions
                    SET status = 'CANCELED',
                        canceled_at = now()
                    WHERE req_id = $1
                      AND status <> 'CANCELED'
                    """,
                    str(req_id),
                )
                return int(res.split()[-1])

    async def get_act_request_transaction(self, *, req_id: str) -> list[dict[str, Any]]:
        async with self._connection() as con:
            async with con.transaction():
                rows = await con.fetch(
                    """
                    SELECT
                        art.*,
                        t.client_id,
                        t.account_id,
                        t.txn_at,
                        t.amount,
                        t.balance_after,
                        t.comment,
                        t.source,
                        a.currency_code,
                        a.precision
                    FROM act_request_transactions art
                    JOIN transactions t ON t.id = art.transaction_id
                    JOIN client_accounts a ON a.id = t.account_id
                    WHERE art.req_id = $1
                    ORDER BY art.id ASC
                    """,
                    str(req_id),
                )
                return [dict(row) for row in rows]
