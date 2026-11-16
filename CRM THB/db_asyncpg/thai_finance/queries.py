from __future__ import annotations

import json
import uuid

import asyncpg

from domain.finance.models import InvalidPosting
from services.finance.outbox import OutboxEvent


def operation_dict(row) -> dict:
    result = dict(row)
    result["command_details"] = json.loads(result["command_details"])
    return result


async def replayed_operation_id(database, operation_id):
    """Resolve a persisted message's operation through technical revaluations."""
    if operation_id is None:
        return None
    return await database.fetchval("""
        WITH RECURSIVE chain AS (
            SELECT id,0 AS depth FROM financial_operations WHERE id=$1
            UNION ALL SELECT o.id,c.depth+1 FROM chain c JOIN financial_operations o
                ON (o.command_details->>'replayed_from')::bigint=c.id AND o.status='posted'
        ) SELECT id FROM chain ORDER BY depth DESC,id DESC LIMIT 1
    """, operation_id)


class FinanceQueries:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def actor_by_telegram_id(self, telegram_id: int) -> int | None:
        return await self._pool.fetchval("SELECT id FROM users WHERE telegram_user_id=$1 AND is_active", telegram_id)

    async def can_read(self, actor_id: int) -> bool:
        return await self._pool.fetchval("""
            SELECT EXISTS(SELECT 1 FROM users u JOIN user_roles r ON r.user_id=u.id
                JOIN role_permissions p ON p.role=r.role
                WHERE u.id=$1 AND u.is_active AND p.permission='finance.read')
        """, actor_id)

    async def cash_balances(self) -> list[dict]:
        rows = await self._pool.fetch("""
            SELECT c.instrument_code,c.ledger_account_id,b.currency_code,b.balance,b.carrying_usdt,b.unvalued_native AS unvalued_quantity
            FROM cash_accounts c JOIN posted_account_balances b ON b.account_id=c.ledger_account_id
            ORDER BY c.instrument_code
        """)
        return [dict(row) for row in rows]

    async def internal_quote(self, instrument, at=None, key=None):
        from db_asyncpg.internal_rates import InternalRates
        return await InternalRates(self._pool).quote(instrument,at,key)

    async def cash_adjustment_accounts(self, instrument):
        row=await self._pool.fetchrow("""
            SELECT c.ledger_account_id AS cash,i.currency_code AS currency,i.precision,a.id AS offset
            FROM cash_accounts c JOIN instruments i ON i.code=c.instrument_code AND i.is_active
            JOIN ledger_accounts a ON a.instrument_code=i.code AND a.purpose='cash_adjustment_offset'
            WHERE c.instrument_code=$1
        """,instrument)
        if row is None:
            raise InvalidPosting('Кассовый счёт не найден')
        return dict(row)

    async def cash_operation_by_message(self, chat_id, message_id):
        operation_id = await self._pool.fetchval("""
            SELECT o.id FROM telegram_message_links l JOIN financial_operations o ON o.id=l.operation_id
            WHERE l.chat_id=$1 AND l.message_id=$2 AND l.purpose='cash_adjustment'
                AND o.kind='manual_journal' AND o.command_details->>'purpose'='cash_adjustment'
                AND (o.command_details->>'chat_id')::bigint=$1
        """,chat_id,message_id)
        return await replayed_operation_id(self._pool, operation_id)

    async def cash_history(self, instrument=None, *, before_id=None, limit=16, since=None, until=None):
        if limit is not None and not 1<=limit<=100:
            raise InvalidPosting('Размер страницы должен быть от 1 до 100')
        if before_id is not None and before_id<=0:
            raise InvalidPosting('Некорректная страница выписки')
        rows=await self._pool.fetch("""
            WITH moves AS (
                SELECT o.id,c.instrument_code,
                    sum(CASE l.side WHEN 'debit' THEN l.native_amount ELSE -l.native_amount END) AS quantity_delta
                FROM journal_lines l JOIN cash_accounts c ON c.ledger_account_id=l.account_id
                JOIN financial_operations o ON o.id=l.operation_id
                WHERE o.status='posted' AND ($1::text IS NULL OR c.instrument_code=$1)
                GROUP BY o.id,c.instrument_code
            ), timeline AS (
                SELECT *,sum(quantity_delta) OVER(PARTITION BY instrument_code ORDER BY id) AS balance_after
                FROM moves
            )
            SELECT o.*,t.instrument_code,t.quantity_delta,t.balance_after,u.display_name AS actor_name
            FROM timeline t JOIN financial_operations o ON o.id=t.id JOIN users u ON u.id=o.created_by
            WHERE t.quantity_delta<>0 AND ($2::bigint IS NULL OR o.id<$2)
                AND ($3::timestamptz IS NULL OR o.effective_at >= $3)
                AND ($4::timestamptz IS NULL OR o.effective_at < $4)
            ORDER BY o.id DESC,t.instrument_code LIMIT $5::bigint
        """,instrument,before_id,since,until,limit)
        return [operation_dict(row) for row in rows]

    async def export_cash_transactions(self, *, since=None, until=None):
        rows=await self.cash_history(limit=None,since=since,until=until)
        return [{'operation_id':row['id'],'txn_at':row['effective_at'],'currency_code':row['instrument_code'],
            'amount':row['quantity_delta'],'balance_after':row['balance_after'],'group_name':'Общая касса',
            'actor_name':row['actor_name'],'source':row['kind'],
            'comment':row['reason'] or row['command_details'].get('comment','')} for row in reversed(rows)]

    async def history(self, *, before_id: int | None = None, limit: int = 50) -> list[dict]:
        if not 1 <= limit <= 100:
            raise InvalidPosting("Размер страницы должен быть от 1 до 100")
        rows = await self._pool.fetch("""
            SELECT id,kind,context,business_date,effective_at,recorded_at,created_by,
                idempotency_key,reversal_of_id,reason,command_details
            FROM financial_operations WHERE status='posted' AND ($1::bigint IS NULL OR id<$1)
            ORDER BY id DESC LIMIT $2
        """, before_id, limit)
        return [operation_dict(row) for row in rows]

    async def detail(self, operation_id: int) -> dict:
        async with self._pool.acquire() as connection:
            operation = await connection.fetchrow("""
                SELECT id,kind,context,business_date,effective_at,recorded_at,created_by,
                    idempotency_key,reversal_of_id,reason,command_details
                FROM financial_operations WHERE id=$1 AND status='posted'
            """, operation_id)
            if operation is None:
                raise InvalidPosting("Операция не найдена")
            rows = await connection.fetch("""
                SELECT l.*,a.code AS account_code,a.purpose,a.instrument_code,
                    p.name AS party_name,f.quoted_rate,f.quoted_direction
                FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id
                LEFT JOIN parties p ON p.id=a.party_id
                LEFT JOIN fx_rate_snapshots f ON f.id=l.rate_snapshot_id
                WHERE l.operation_id=$1 ORDER BY l.line_number
            """, operation_id)
            note = await connection.fetchval("SELECT note FROM operation_audit_notes WHERE operation_id=$1", operation_id)
            return {**operation_dict(operation), "lines": [dict(row) for row in rows], "note": note}


class OutboxDeliveryRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def claim(self) -> OutboxEvent | None:
        token = uuid.uuid4()
        row = await self._pool.fetchrow("""
            WITH candidate AS (
                SELECT e.id FROM telegram_outbox e LEFT JOIN financial_operations o ON o.id=e.operation_id
                WHERE (o.status='posted' OR (e.operation_id IS NULL AND e.event_type IN ('cash.request_changed','payment.watch_changed'))) AND (
                    (e.status IN ('pending','failed') AND e.available_at<=clock_timestamp())
                    OR (e.status='processing' AND e.lease_until<=clock_timestamp()))
                ORDER BY e.available_at,e.id LIMIT 1 FOR UPDATE OF e SKIP LOCKED
            ) UPDATE telegram_outbox e SET status='processing',attempts=attempts+1,
                lease_until=clock_timestamp()+interval '60 seconds',lease_token=$1
            FROM candidate WHERE e.id=candidate.id RETURNING e.*
        """, token)
        if row is None:
            return None
        return OutboxEvent(row["id"], row["event_key"], row["event_type"], row["payload"], str(token))

    async def delivered(self, event: OutboxEvent) -> None:
        await self._pool.execute("""
            UPDATE telegram_outbox SET status='sent',lease_until=NULL,lease_token=NULL,last_error=NULL
            WHERE id=$1 AND lease_token=$2 AND status='processing'
        """, event.id, uuid.UUID(event.lease_token))

    async def retry(self, event: OutboxEvent, error: str) -> None:
        await self._pool.execute("""
            UPDATE telegram_outbox SET status='failed',lease_until=NULL,lease_token=NULL,
                available_at=clock_timestamp()+interval '30 seconds',last_error=$3
            WHERE id=$1 AND lease_token=$2 AND status='processing'
        """, event.id, uuid.UUID(event.lease_token), error)
