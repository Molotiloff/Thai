import json

import asyncpg

from db_asyncpg.thai_finance.queries import operation_dict
from db_asyncpg.thai_trading.workspace import ENTRIES
from domain.finance.models import InvalidPosting


class ClientQueries:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def is_manager(self, user_id):
        return await self._pool.fetchval("""
            SELECT EXISTS(SELECT 1 FROM users u JOIN user_roles r ON r.user_id=u.id
                WHERE u.telegram_user_id=$1 AND u.is_active AND r.role IN ('manager','accountant','owner','admin'))
        """,user_id)

    async def list_managers(self):
        return [dict(row) for row in await self._pool.fetch("""
            SELECT u.telegram_user_id AS user_id,u.display_name FROM users u JOIN user_roles r ON r.user_id=u.id
            WHERE u.is_active AND r.role='manager' AND u.telegram_user_id IS NOT NULL ORDER BY u.telegram_user_id
        """)]

    async def find_client_by_name_exact(self,name):
        normalized=name.strip().lower()
        return next((row for row in await self._active_chat_clients()
                     if row['name'].strip().lower()==normalized),None)

    async def _active_chat_clients(self):
        return [dict(row) for row in await self._pool.fetch("""
            SELECT c.id,p.name,l.telegram_chat_id AS chat_id FROM clients c JOIN parties p ON p.id=c.party_id
            LEFT JOIN client_chat_links l ON l.client_id=c.id AND l.is_active
            WHERE c.is_active ORDER BY c.id,l.id
        """)]

    async def archive_chat_matches(self,normalized):
        # PostgreSQL installations with C locale do not lowercase Cyrillic.
        # Match the same Unicode normalization used by the archive middleware.
        rows = await self._active_chat_clients()
        return [{'chat_id': row['chat_id'], 'exact': row['name'].strip().lower() == normalized}
                for row in rows if row['chat_id'] is not None and normalized in row['name'].strip().lower()]

    async def chat_name(self, chat_id):
        return await self._pool.fetchval("""
            SELECT p.name FROM client_chat_links l JOIN clients c ON c.id=l.client_id
            JOIN parties p ON p.id=c.party_id WHERE l.telegram_chat_id=$1
        """, chat_id)

    async def groups(self):
        return [dict(row) for row in await self._pool.fetch('SELECT code,name,exposes_in_loans FROM client_groups ORDER BY code')]

    async def clients(self, *, after_id=None, limit=50):
        return [dict(row) for row in await self._pool.fetch("""
            SELECT c.*,p.name FROM clients c JOIN parties p ON p.id=c.party_id
            WHERE ($1::bigint IS NULL OR c.id>$1) ORDER BY c.id LIMIT $2
        """, after_id, limit)]

    async def workspace(self, client_id=None):
        """Consistent customer directory with ledger valuations, without per-client requests."""
        async with self._pool.acquire() as db, db.transaction(isolation='repeatable_read', readonly=True):
            clients = await db.fetch("""
                SELECT c.*,p.name,g.name AS group_name,l.telegram_chat_id,l.invite_link
                FROM clients c JOIN parties p ON p.id=c.party_id
                LEFT JOIN client_groups g ON g.code=c.group_code
                LEFT JOIN LATERAL (SELECT * FROM client_chat_links WHERE client_id=c.id
                    AND is_active ORDER BY id LIMIT 1) l ON TRUE
                WHERE ($1::bigint IS NULL OR c.id=$1) ORDER BY c.id
            """, client_id)
            wallets = await db.fetch("""
                SELECT w.client_id,w.instrument_code,b.balance,b.unvalued_native AS unvalued_quantity FROM client_wallet_accounts w
                JOIN posted_account_balances b ON b.account_id=w.ledger_account_id
                WHERE w.kind='client' AND ($1::bigint IS NULL OR w.client_id=$1)
                ORDER BY w.instrument_code
            """, client_id)
            summary = await db.fetch(ENTRIES + """
                SELECT e.client_id,count(*) AS operations_count,
                    count(*) FILTER(WHERE e.deal_id IS NOT NULL) AS exchanges_count,
                    coalesce(sum(j.amount_usdt) FILTER(WHERE e.deal_id IS NOT NULL),0) AS turnover_usdt,
                    coalesce(sum(j.amount_usdt) FILTER(WHERE e.kind='purchase'),0) AS purchase_usdt,
                    coalesce(sum(j.amount_usdt) FILTER(WHERE e.kind='sale'),0) AS sale_usdt,
                    (array_agg(e.created_by ORDER BY e.created_at DESC,e.entry_id DESC))[1] AS manager
                FROM selected e LEFT JOIN journal_lines j ON j.operation_id=e.operation_id AND j.line_number=
                    (SELECT min(jl.line_number) FROM journal_lines jl WHERE jl.operation_id=e.operation_id AND NOT jl.valuation_component)
                WHERE e.status='posted' AND ($4::bigint IS NULL OR e.client_id=$4)
                GROUP BY e.client_id
            """, '', None, None, client_id)
            recent = await db.fetch(ENTRIES + """
                SELECT * FROM (SELECT e.*,j.amount_usdt,
                    row_number() OVER(PARTITION BY e.client_id ORDER BY e.created_at DESC,e.entry_id DESC) AS rank
                    FROM selected e LEFT JOIN journal_lines j ON j.operation_id=e.operation_id AND j.line_number=
                        (SELECT min(jl.line_number) FROM journal_lines jl WHERE jl.operation_id=e.operation_id AND NOT jl.valuation_component)
                    WHERE e.client_id IS NOT NULL AND ($4::bigint IS NULL OR e.client_id=$4)) r
                WHERE rank<=5 ORDER BY created_at DESC,entry_id DESC
            """, '', None, None, client_id)
            wallet_map, recent_map = {}, {}
            for row in wallets:
                wallet_map.setdefault(row['client_id'], []).append(dict(row))
            for row in recent:
                recent_map.setdefault(row['client_id'], []).append({**dict(row), 'attributes': json.loads(row['attributes'])})
            stats = {row['client_id']: dict(row) for row in summary}
            return {'clients': [{**dict(row), 'wallets': wallet_map.get(row['id'], []),
                'stats': stats.get(row['id'], {}), 'recent': recent_map.get(row['id'], [])} for row in clients]}

    async def wallet_accounts(self, client_id, instrument):
        row = await self._pool.fetchrow("""
            SELECT w.ledger_account_id AS wallet,cash.ledger_account_id AS cash,
                i.currency_code AS currency,i.precision FROM client_wallet_accounts w
            JOIN instruments i ON i.code=w.instrument_code AND i.is_active
            JOIN cash_accounts cash ON cash.instrument_code=i.code
            WHERE w.client_id=$1 AND w.instrument_code=$2 AND w.kind='client'
        """, client_id, instrument)
        if row is None:
            raise InvalidPosting('Клиентский кошелёк или инструмент не найден')
        result = dict(row)
        for account in await self._pool.fetch("SELECT id,purpose FROM ledger_accounts WHERE purpose IN ('fx_income','fx_loss')"):
            result[account['purpose']] = account['id']
        return result

    async def internal_quote(self, instrument, at=None, key=None):
        from db_asyncpg.internal_rates import InternalRates
        return await InternalRates(self._pool).quote(instrument,at,key)

    async def adjustment_accounts(self, client_id, instrument):
        result = await self.wallet_accounts(client_id, instrument)
        result["retained_result"] = await self._pool.fetchval(
            "SELECT id FROM ledger_accounts WHERE purpose='retained_result'"
        )
        result["adjustment_offset"] = await self._pool.fetchval(
            "SELECT id FROM ledger_accounts WHERE purpose='wallet_adjustment_offset' AND instrument_code=$1",instrument)
        return result

    async def find_by_name(self, name):
        return [dict(row) for row in await self._pool.fetch("""
            SELECT c.id,p.name FROM clients c JOIN parties p ON p.id=c.party_id
            WHERE c.is_active AND lower(btrim(p.name))=lower(btrim($1)) ORDER BY c.id
        """, name)]

    async def nonzero_wallets(self, instrument=None, sign=None, limit=100):
        return [dict(row) for row in await self._pool.fetch("""
            SELECT c.id,p.name,w.instrument_code,b.balance FROM client_wallet_accounts w
            JOIN clients c ON c.id=w.client_id JOIN parties p ON p.id=c.party_id
            JOIN posted_account_balances b ON b.account_id=w.ledger_account_id
            WHERE c.is_active AND w.kind='client' AND b.balance<>0
              AND ($1::text IS NULL OR w.instrument_code=$1)
              AND ($2::text IS NULL OR ($2='+' AND b.balance>0) OR ($2='-' AND b.balance<0))
            ORDER BY c.id,w.instrument_code LIMIT $3
        """, instrument, sign, limit)]

    async def wallets(self, client_id, *, connection=None):
        db = connection if connection is not None else self._pool
        if not await db.fetchval('SELECT EXISTS(SELECT 1 FROM clients WHERE id=$1)', client_id):
            raise InvalidPosting('Клиент не найден')
        return [dict(row) for row in await db.fetch("""
            SELECT w.client_id,w.instrument_code,w.ledger_account_id,b.currency_code,b.balance,b.carrying_usdt,b.unvalued_native AS unvalued_quantity,i.precision
            FROM client_wallet_accounts w JOIN posted_account_balances b ON b.account_id=w.ledger_account_id
            JOIN instruments i ON i.code=w.instrument_code
            WHERE w.client_id=$1 AND w.kind='client' ORDER BY w.instrument_code
        """, client_id)]

    async def loans(self, *, client_id=None, after_client_id=None, limit=50, include_zero=False):
        return [dict(row) for row in await self._pool.fetch("""
            WITH page AS (SELECT DISTINCT client_id FROM partner_loan_balances
                WHERE ($1::bigint IS NULL OR client_id=$1) AND ($2::bigint IS NULL OR client_id>$2)
                    AND ($4::boolean OR balance<>0) ORDER BY client_id LIMIT $3)
            SELECT b.*,p.name FROM partner_loan_balances b JOIN page ON page.client_id=b.client_id
                JOIN parties p ON p.id=b.party_id WHERE ($4::boolean OR b.balance<>0)
            ORDER BY b.client_id,b.instrument_code
        """, client_id, after_client_id, limit, include_zero)]

    async def loan_totals(self, client_id=None):
        async with self._pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                rows = await connection.fetch("""
                    SELECT i.code AS instrument_code,i.currency_code,coalesce(sum(b.balance),0) AS balance,
                        coalesce(sum(b.carrying_usdt),0) AS carrying_usdt,
                        coalesce(sum(greatest(b.balance,0)),0) AS exchange_owes,
                        coalesce(sum(greatest(-b.balance,0)),0) AS clients_owe
                    FROM instruments i LEFT JOIN partner_loan_balances b ON b.instrument_code=i.code
                        AND ($1::bigint IS NULL OR b.client_id=$1)
                    GROUP BY i.code,i.currency_code ORDER BY i.code
                """, client_id)
                currency_rows = await connection.fetch("""
                    SELECT i.currency_code,coalesce(sum(b.balance),0) AS balance,
                        coalesce(sum(b.carrying_usdt),0) AS carrying_usdt,
                        coalesce(sum(greatest(b.balance,0)),0) AS exchange_owes,
                        coalesce(sum(greatest(-b.balance,0)),0) AS clients_owe
                    FROM instruments i LEFT JOIN partner_loan_balances b ON b.instrument_code=i.code
                        AND ($1::bigint IS NULL OR b.client_id=$1)
                    GROUP BY i.currency_code ORDER BY i.currency_code
                """, client_id)
                return {'by_instrument': [dict(row) for row in rows], 'by_currency': [dict(row) for row in currency_rows]}

    async def wallet_history(self, client_id, instrument, before_id=None, limit=50):
        async with self._pool.acquire() as connection:
            return await self._wallet_history(connection, client_id, instrument, before_id, limit)

    @staticmethod
    def _wallet_timeline():
        return """
            WITH movements AS (
                SELECT w.instrument_code,l.operation_id,
                    sum(CASE l.side WHEN 'credit' THEN l.native_amount ELSE -l.native_amount END) AS quantity_delta,
                    sum(CASE l.side WHEN 'credit' THEN l.amount_usdt ELSE -l.amount_usdt END) AS carrying_delta_usdt
                FROM client_wallet_accounts w JOIN journal_lines l ON l.account_id=w.ledger_account_id
                JOIN financial_operations o ON o.id=l.operation_id
                WHERE w.client_id=$1 AND w.kind='client' AND o.status='posted'
                    AND ($2::text IS NULL OR w.instrument_code=$2)
                GROUP BY w.instrument_code,l.operation_id
            ), timeline AS (
                SELECT *,sum(quantity_delta) OVER(PARTITION BY instrument_code ORDER BY operation_id) AS balance_after,
                    sum(carrying_delta_usdt) OVER(PARTITION BY instrument_code ORDER BY operation_id) AS carrying_after_usdt
                FROM movements
            )
        """

    async def _wallet_history(self, connection, client_id, instrument, before_id, limit):
        if not 1<=limit<=100:
            raise InvalidPosting('Размер страницы должен быть от 1 до 100')
        if not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM client_wallet_accounts WHERE client_id=$1 AND instrument_code=$2 AND kind='client')",client_id,instrument):
            raise InvalidPosting('Кошелёк не найден')
        rows = await connection.fetch(self._wallet_timeline()+"""
            SELECT o.id,o.kind,o.business_date,o.effective_at,o.recorded_at,o.created_by,o.idempotency_key,
                o.reason,o.reversal_of_id,o.command_details,t.quantity_delta,t.carrying_delta_usdt,
                t.balance_after,t.carrying_after_usdt FROM timeline t JOIN financial_operations o ON o.id=t.operation_id
            WHERE t.quantity_delta<>0 AND ($3::bigint IS NULL OR o.id<$3) ORDER BY o.id DESC LIMIT $4
        """,client_id,instrument,before_id,limit)
        return [operation_dict(row) for row in rows]

    async def export_transactions(self, *, client_id, since=None, until=None):
        async with self._pool.acquire() as db, db.transaction(isolation='repeatable_read',readonly=True):
            if not await db.fetchval('SELECT EXISTS(SELECT 1 FROM clients WHERE id=$1)',client_id):
                raise InvalidPosting('Клиент не найден')
            rows = await db.fetch(self._wallet_timeline()+"""
                SELECT o.id AS operation_id,o.effective_at AS txn_at,t.instrument_code AS currency_code,
                    i.precision,t.quantity_delta AS amount,t.balance_after,g.name AS group_name,
                    u.display_name AS actor_name,o.kind AS source,
                    coalesce(o.reason,o.command_details->>'comment','') AS comment
                FROM timeline t JOIN financial_operations o ON o.id=t.operation_id
                JOIN instruments i ON i.code=t.instrument_code
                JOIN users u ON u.id=o.created_by JOIN clients c ON c.id=$1
                LEFT JOIN client_groups g ON g.code=c.group_code
                WHERE t.quantity_delta<>0 AND ($3::timestamptz IS NULL OR o.effective_at >= $3)
                    AND ($4::timestamptz IS NULL OR o.effective_at < $4)
                ORDER BY o.id,t.instrument_code
            """,client_id,None,since,until)
            return [dict(row) for row in rows]

    async def loan_wallet_history(self, client_id, instrument, before_id, limit):
        async with self._pool.acquire() as connection:
            async with connection.transaction(isolation='repeatable_read', readonly=True):
                if not await connection.fetchval("""
                    SELECT EXISTS(SELECT 1 FROM clients WHERE id=$1 AND is_active AND group_code='partner')
                """, client_id):
                    raise InvalidPosting('Клиент не относится к группе Партнер; обычная история доступна в кошельке')
                return await self._wallet_history(connection, client_id, instrument, before_id, limit)


    async def transfer_request(self, key):
        from db_asyncpg.thai_clients.repository import ClientsRepository
        async with self._pool.acquire() as db:
            event = await ClientsRepository(db).find_event(key)
        if event is None:
            raise InvalidPosting('Заявка перевода не найдена; создайте новую командой /перевод')
        return event['result']

    async def transfer_context(self, operation_id):
        from db_asyncpg.thai_clients.repository import ClientsRepository
        async with self._pool.acquire() as db, db.transaction(isolation='repeatable_read',readonly=True):
            return await ClientsRepository(db).transfer_context(operation_id)

    async def transfer_recipients(self, name):
        return [dict(row) for row in await self._pool.fetch("""
            SELECT c.id,p.name,l.telegram_chat_id AS chat_id FROM clients c JOIN parties p ON p.id=c.party_id
            JOIN LATERAL (SELECT telegram_chat_id FROM client_chat_links WHERE client_id=c.id AND is_active
                ORDER BY id LIMIT 1) l ON TRUE
            WHERE c.is_active AND lower(btrim(p.name))=lower(btrim($1)) ORDER BY c.id
        """, name)]

    async def transfer_by_message(self, chat_id, message_id):
        return await self._pool.fetchval("""
            SELECT o.id FROM telegram_message_links t JOIN financial_operations o ON o.id=t.operation_id
            WHERE t.chat_id=$1 AND t.message_id=$2 AND o.kind='wallet_transfer'
                AND t.purpose IN ('client_transfer_receipt_posted','client_transfer_receipt_cancelled')
        """, chat_id,message_id)


    async def wallet_operation_by_message(self, chat_id, message_id):
        return await self._pool.fetchval("""
            SELECT l.operation_id FROM telegram_message_links l JOIN financial_operations o ON o.id=l.operation_id
            WHERE l.chat_id=$1 AND l.message_id=$2 AND l.purpose='wallet_adjustment'
                AND o.kind='manual_journal' AND o.command_details->>'purpose'='wallet_adjustment'
        """,chat_id,message_id)

    async def zero_preview(self, client_id):
        from db_asyncpg.thai_clients.repository import ClientsRepository
        async with self._pool.acquire() as db, db.transaction(isolation='repeatable_read',readonly=True):
            return await ClientsRepository(db).zero_preview(client_id)

    async def directory_event(self, key):
        from db_asyncpg.thai_clients.repository import ClientsRepository
        async with self._pool.acquire() as db:
            event = await ClientsRepository(db).find_event(key)
        if event is None:
            raise InvalidPosting('Подтверждение не найдено; повторите команду')
        return event['result']

    async def list_clients(self):
        return await self.list_clients_by_group(None)

    async def list_clients_by_group(self, group):
        return [dict(row) for row in await self._pool.fetch("""
            SELECT c.id,p.name,g.name AS client_group,l.telegram_chat_id AS chat_id
            FROM clients c JOIN parties p ON p.id=c.party_id JOIN client_groups g ON g.code=c.group_code
            LEFT JOIN LATERAL (SELECT telegram_chat_id FROM client_chat_links WHERE client_id=c.id AND is_active
                ORDER BY id LIMIT 1) l ON TRUE WHERE c.is_active
                AND ($1::text IS NULL OR replace(lower(g.name),'ё','е')=replace(lower($1),'ё','е') OR g.code=lower($1))
            ORDER BY lower(p.name),c.id
        """,group)]

    async def balances_by_client(self):
        return [dict(row) for row in await self._pool.fetch("""
            SELECT c.id AS client_id,p.name AS client_name,g.name AS client_group,
                l.telegram_chat_id AS chat_id,w.instrument_code AS currency_code,b.balance,i.precision
            FROM clients c JOIN parties p ON p.id=c.party_id JOIN client_groups g ON g.code=c.group_code
            JOIN client_wallet_accounts w ON w.client_id=c.id AND w.kind='client'
            JOIN posted_account_balances b ON b.account_id=w.ledger_account_id
            JOIN instruments i ON i.code=w.instrument_code LEFT JOIN LATERAL (
                SELECT telegram_chat_id FROM client_chat_links WHERE client_id=c.id AND is_active ORDER BY id LIMIT 1
            ) l ON TRUE WHERE c.is_active ORDER BY c.id,w.instrument_code
        """)]
