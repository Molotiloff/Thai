import json

import asyncpg

from domain.finance.models import InvalidPosting


class TradingQueries:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def accounts(self, client_id, receive, pay, mode, *, connection=None):
        if mode not in ("cash", "wallet") or receive == pay:
            raise InvalidPosting("Выберите cash/wallet и два разных инструмента")
        result = {"mode": mode}
        db = connection if connection is not None else self._pool
        for side, instrument in (("receive", receive), ("pay", pay)):
            row = await db.fetchrow(
                """
                SELECT i.code,i.currency_code AS currency,i.precision,c.ledger_account_id AS cash,
                    w.ledger_account_id AS wallet FROM instruments i JOIN cash_accounts c ON c.instrument_code=i.code
                LEFT JOIN client_wallet_accounts w ON w.instrument_code=i.code AND w.client_id=$2 AND w.kind='client'
                WHERE i.code=$1 AND i.is_active
            """,
                instrument,
                client_id,
            )
            if row is None or (mode == "wallet" and row["wallet"] is None):
                raise InvalidPosting("Инструмент или клиентский кошелёк не найден")
            result[side] = dict(row)
        for row in await db.fetch(
            "SELECT id,purpose FROM ledger_accounts WHERE purpose IN ('trading_income','trading_loss','transfer_fee_income')"
        ):
            result[row["purpose"]] = row["id"]
        return result

    async def detail(self, deal_id, *, connection=None):
        db = connection if connection is not None else self._pool
        row = await db.fetchrow("SELECT * FROM effective_deals WHERE id=$1", deal_id)
        if row is None:
            raise InvalidPosting("Сделка не найдена")
        result = dict(row)
        result["attributes"] = json.loads(row["attributes"])
        if result['kind']=='cash_exchange':
            result['cash_confirmations'] = [dict(item) for item in await db.fetch(
                'SELECT * FROM cash_exchange_confirmations WHERE deal_id=$1 ORDER BY direction',deal_id)]
        if (result['kind'] in ('deposit','withdrawal') and result['status']=='posted'
            and not await db.fetchval('SELECT EXISTS(SELECT 1 FROM active_financial_originals WHERE id=$1)', result['operation_id'])):
            result['effective_status'] = 'cancelled'
        if result['kind'] in ('deposit', 'withdrawal','cash_exchange'):
            result['status_at'] = await db.fetchval(
                'SELECT effective_at FROM financial_operations WHERE id=$1',
                result['reversal_operation_id'] or result['operation_id'])
            if result['status_at'] is None:
                result['status_at'] = result['attributes'].get('status_at')
        return result

    async def list(self, before_id=None, limit=50, client_id=None):
        rows = await self._pool.fetch(
            "SELECT * FROM effective_deals WHERE ($1::bigint IS NULL OR id<$1) AND ($3::bigint IS NULL OR client_id=$3) ORDER BY id DESC LIMIT $2",
            before_id,
            limit,
            client_id,
        )
        return [{**dict(row), "attributes": json.loads(row["attributes"])} for row in rows]

    async def exchange_context(self, deal_id, *, connection=None):
        db = connection if connection is not None else self._pool
        root_id = await db.fetchval("""
            WITH RECURSIVE parents AS (
                SELECT id,0 AS depth FROM deals WHERE id=$1 AND kind='fx_trade'
                UNION ALL SELECT r.deal_id,p.depth+1 FROM parents p
                    JOIN deal_reversals r ON r.replacement_deal_id=p.id
            ) SELECT id FROM parents ORDER BY depth DESC LIMIT 1
        """, deal_id)
        if root_id is None:
            raise InvalidPosting('Это не обменная заявка')
        active_id = await db.fetchval("""
            WITH RECURSIVE chain AS (
                SELECT id,0 AS depth FROM deals WHERE id=$1
                UNION ALL SELECT r.replacement_deal_id,c.depth+1 FROM chain c
                    JOIN deal_reversals r ON r.deal_id=c.id WHERE r.replacement_deal_id IS NOT NULL
            ) SELECT id FROM chain ORDER BY depth DESC LIMIT 1
        """, root_id)
        root = await self.detail(root_id, connection=db)
        active = root if active_id==root_id else await self.detail(active_id, connection=db)
        status_at = await db.fetchval('SELECT effective_at FROM financial_operations WHERE id=$1',
            active['reversal_operation_id'] or active['operation_id'])
        name = await db.fetchval('SELECT p.name FROM clients c JOIN parties p ON p.id=c.party_id WHERE c.id=$1',root['client_id'])
        return {'root':root, 'active':active, 'status_at':status_at, 'client_name':name}

    async def exchange_by_reference(self, reference):
        row = await self._pool.fetchval("SELECT id FROM deals WHERE external_reference=$1 AND kind='fx_trade'",reference)
        return await self.detail(row) if row else None

    async def exchange_by_message(self, chat_id, message_id):
        return await self._pool.fetchval("""
            SELECT deal_id FROM telegram_message_links WHERE chat_id=$1 AND message_id=$2
                AND purpose IN ('exchange_client','exchange_request')
            UNION ALL
            SELECT (o.command_details->>'deal_id')::bigint FROM telegram_deliveries t
                JOIN telegram_outbox e ON e.event_key=t.event_key
                JOIN financial_operations o ON o.id=e.operation_id
            WHERE t.chat_id=$1 AND t.message_id=$2 AND o.kind='fx_trade' LIMIT 1
        """, chat_id, message_id)

    async def rates(self, *, at=None):
        rows = await self._pool.fetch("""
            SELECT i.code AS instrument,i.currency_code,q.quoted_rate,q.quoted_direction,q.usdt_per_unit,q.effective_at
            FROM instruments i LEFT JOIN LATERAL (
                SELECT r.* FROM market_quotes m JOIN fx_rate_snapshots r ON r.id=m.rate_snapshot_id
                    JOIN active_financial_originals o ON o.id=m.operation_id
                WHERE m.instrument_code=i.code AND r.effective_at<=coalesce($1::timestamptz,clock_timestamp())
                ORDER BY r.effective_at DESC,m.id DESC LIMIT 1
            ) q ON TRUE WHERE i.is_active ORDER BY i.code
        """, at)
        return [dict(row) for row in rows]

    async def client_by_chat(self, chat_id):
        return await self._pool.fetchval(
            "SELECT client_id FROM client_chat_links WHERE telegram_chat_id=$1 AND is_active",
            chat_id,
        )


    async def payment_watch(self, watch_id, *, connection=None):
        db = connection if connection is not None else self._pool
        row = await db.fetchrow('SELECT * FROM payment_watches WHERE id=$1',watch_id)
        if row is None:
            raise InvalidPosting('Наблюдение не найдено')
        events = await db.fetch('SELECT * FROM payment_watch_events WHERE watch_id=$1 ORDER BY id',watch_id)
        return {**dict(row),'events':[dict(event) for event in events]}

    async def payment_watches(self):
        return [dict(row) for row in await self._pool.fetch("SELECT * FROM payment_watches WHERE status='WATCHING' ORDER BY id")]

    async def payment_wallet(self, fallback=None):
        row = await self._pool.fetchval("SELECT value #>> '{}' FROM crm_settings WHERE key='usdt_wallet'")
        return row or fallback
