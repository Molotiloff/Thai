import hashlib
import json

import asyncpg

from domain.finance.models import InvalidPosting, PermissionDenied, canonical_json


class ClientsRepository:
    def __init__(self, connection: asyncpg.Connection):
        self._db = connection

    async def change_manager(self, telegram_user_id, display_name, enabled):
        if telegram_user_id<=0 or telegram_user_id>9223372036854775807:
            raise InvalidPosting('Telegram ID должен быть положительным')
        await self._db.execute("SELECT pg_advisory_xact_lock(hashtextextended('staff:'||$1::bigint::text,0))",telegram_user_id)
        row=await self._db.fetchrow('SELECT id,is_active FROM users WHERE telegram_user_id=$1 FOR UPDATE',telegram_user_id)
        if enabled:
            if row is not None and not row['is_active']:
                raise InvalidPosting('Сотрудник отключён. Сначала явно восстановите его доступ в CRM.')
            user_id=row['id'] if row is not None else await self._db.fetchval(
                'INSERT INTO users(telegram_user_id,display_name) VALUES ($1,$2) RETURNING id',
                telegram_user_id,display_name or str(telegram_user_id))
            if display_name:
                await self._db.execute('UPDATE users SET display_name=$2 WHERE id=$1',user_id,display_name)
            await self._db.execute("INSERT INTO user_roles(user_id,role) VALUES ($1,'manager') ON CONFLICT DO NOTHING",user_id)
            return {'changed':True,'user_id':user_id}
        if row is None:
            return {'changed':False}
        changed=await self._db.fetchval("DELETE FROM user_roles WHERE user_id=$1 AND role='manager' RETURNING user_id",row['id'])
        return {'changed':changed is not None,'user_id':row['id']}

    async def guard_directory(self, actor_id, key, permission='client.manage'):
        permitted = await self._db.fetchval("""
            SELECT EXISTS(SELECT 1 FROM users u JOIN user_roles r ON r.user_id=u.id
                JOIN role_permissions p ON p.role=r.role WHERE u.id=$1 AND u.is_active AND p.permission=$2)
        """, actor_id, permission)
        if not permitted:
            raise PermissionDenied('Недостаточно прав для изменения клиентов и групп')
        await self._db.execute("SELECT pg_advisory_xact_lock(hashtextextended('clients:'||$1,0))", key)

    async def find_event(self, key):
        row = await self._db.fetchrow('SELECT fingerprint,result FROM client_directory_events WHERE idempotency_key=$1', key)
        return {'fingerprint': row['fingerprint'], 'result': json.loads(row['result'])} if row else None

    async def save_event(self, key, fingerprint, kind, actor_id, payload, result):
        await self._db.execute("""
            INSERT INTO client_directory_events(idempotency_key,fingerprint,kind,actor_id,payload,result)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb)
        """, key, fingerprint, kind, actor_id, canonical_json(payload), canonical_json(result))

    async def create_group(self, code, name):
        await self._db.execute("INSERT INTO client_groups(code,name,exposes_in_loans) VALUES ($1,$2,$3)", code, name, code=='partner')
        return {'code': code, 'name': name, 'exposes_in_loans': code=='partner'}

    async def rename_group(self, code, name):
        row = await self._db.fetchrow('SELECT * FROM client_groups WHERE code=$1 FOR UPDATE', code)
        if row is None:
            raise InvalidPosting('Группа не найдена')
        await self._db.execute('UPDATE client_groups SET name=$2 WHERE code=$1', code, name)
        return {'code': code, 'previous_name': row['name'], 'name': name, 'exposes_in_loans': row['exposes_in_loans']}

    async def _group(self, code):
        if not await self._db.fetchval('SELECT EXISTS(SELECT 1 FROM client_groups WHERE code=$1)', code):
            raise InvalidPosting('Группа не найдена; используется стабильный code, а не имя')

    async def create_client(self, name, party_id, group_code):
        await self._group(group_code)
        if party_id is None:
            party_id = await self._db.fetchval("INSERT INTO parties(name,kind) VALUES ($1,'person') RETURNING id", name.strip())
        # Serialize registration of one participant independently of command keys.
        party = await self._db.fetchval('SELECT id FROM parties WHERE id=$1 AND is_active FOR UPDATE', party_id)
        if party is None:
            raise InvalidPosting('Участник не найден')
        client_id = await self._db.fetchval('INSERT INTO clients(party_id,group_code) VALUES ($1,$2) RETURNING id', party_id, group_code)
        await self._db.execute("INSERT INTO party_roles VALUES ($1,'client') ON CONFLICT DO NOTHING", party_id)
        await self._ensure_wallets(client_id, party_id)
        return {'client_id': client_id, 'party_id': party_id, 'group_code': group_code}

    async def _ensure_wallets(self, client_id, party_id):
        instruments = await self._db.fetch('SELECT code,currency_code FROM instruments WHERE is_active ORDER BY code')
        for instrument in instruments:
            if await self._db.fetchval(
                'SELECT EXISTS(SELECT 1 FROM client_wallet_accounts WHERE client_id=$1 AND instrument_code=$2)',
                client_id, instrument['code'],
            ):
                continue
            account_id = await self._db.fetchval("""
                INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,party_id,currency_code,instrument_code)
                VALUES ($1,'liability','client_wallet','credit',$2,$3,$4) RETURNING id
            """, f"wallet:{client_id}:{instrument['code']}", party_id, instrument['currency_code'], instrument['code'])
            await self._db.execute('INSERT INTO client_wallet_accounts(client_id,instrument_code,ledger_account_id) VALUES ($1,$2,$3)', client_id, instrument['code'], account_id)
            await self._db.execute('INSERT INTO account_balances(account_id) VALUES ($1)', account_id)

    async def lock_chat(self, chat_id):
        await self._db.execute("SELECT pg_advisory_xact_lock(hashtextextended('client_chat:'||$1::bigint::text,0))", chat_id)

    async def find_bootstrap(self, key):
        row = await self._db.fetchrow('SELECT fingerprint,client_id FROM telegram_client_bootstrap_events WHERE idempotency_key=$1', key)
        return dict(row) if row else None

    async def save_bootstrap(self, key, fingerprint, telegram_user_id, chat_id, client_id, name):
        await self._db.execute("""
            INSERT INTO telegram_client_bootstrap_events
              (idempotency_key,fingerprint,telegram_user_id,telegram_chat_id,client_id,name_snapshot)
            VALUES ($1,$2,$3,$4,$5,$6)
        """, key, fingerprint, telegram_user_id, chat_id, client_id, name)

    async def ensure_chat_client(self, chat_id, name, *, preserve_name=False):
        await self.lock_chat(chat_id)
        row = await self._db.fetchrow("""
            SELECT c.id,c.party_id,c.is_active,l.is_active AS link_active
            FROM client_chat_links l JOIN clients c ON c.id=l.client_id
            WHERE l.telegram_chat_id=$1 FOR UPDATE OF c,l
        """, chat_id)
        if row:
            if not row['is_active'] or not row['link_active']:
                raise InvalidPosting('Этот клиентский чат закрыт. Обратитесь к менеджеру.')
            if not preserve_name:
                await self._db.execute('UPDATE parties SET name=$2 WHERE id=$1 AND name<>$2', row['party_id'], name)
            await self._ensure_wallets(row['id'], row['party_id'])
            return row['id']
        result = await self.create_client(name, None, 'balance')
        await self.bind_chat(result['client_id'], chat_id)
        return result['client_id']

    async def lock_client(self, client_id):
        row = await self._db.fetchrow('SELECT * FROM clients WHERE id=$1 FOR UPDATE', client_id)
        if row is None or not row['is_active']:
            raise InvalidPosting('Клиент не найден или закрыт')
        return dict(row)

    async def assign_group(self, client_id, group_code):
        await self._group(group_code)
        client = await self.lock_client(client_id)
        await self._db.execute('UPDATE clients SET group_code=$2 WHERE id=$1', client_id, group_code)
        return {'client_id': client_id, 'previous_group_code': client['group_code'], 'group_code': group_code}

    async def bind_chat(self, client_id, chat_id):
        await self.lock_chat(chat_id)
        await self.lock_client(client_id)
        if not chat_id:
            raise InvalidPosting('ID Telegram-чата не может быть нулевым')
        await self._db.execute("""
            INSERT INTO client_chat_links(client_id,telegram_chat_id) VALUES ($1,$2)
            ON CONFLICT(telegram_chat_id) DO UPDATE SET is_active=TRUE
            WHERE client_chat_links.client_id=EXCLUDED.client_id
        """, client_id, chat_id)
        if await self._db.fetchval('SELECT client_id FROM client_chat_links WHERE telegram_chat_id=$1', chat_id) != client_id:
            raise InvalidPosting('Чат уже принадлежит другому клиенту')
        return {'client_id':client_id, 'telegram_chat_id':chat_id}

    async def set_invite_link(self, client_id, invite_link):
        await self.lock_client(client_id)
        rows = await self._db.fetch("""
            UPDATE client_chat_links SET invite_link=$2 WHERE client_id=$1 AND is_active
            RETURNING telegram_chat_id
        """, client_id, invite_link)
        if not rows:
            raise InvalidPosting('Сначала привяжите Telegram-чат')
        return {'client_id': client_id, 'invite_link': invite_link}

    async def close_client(self, client_id):
        await self.lock_client(client_id)
        await self._db.fetch("""
            SELECT a.id FROM ledger_accounts a JOIN client_wallet_accounts w ON w.ledger_account_id=a.id
            WHERE w.client_id=$1 ORDER BY a.id FOR UPDATE OF a
        """,client_id)
        if await self._db.fetchval("""
            SELECT EXISTS(SELECT 1 FROM client_wallet_accounts w JOIN posted_account_balances b ON b.account_id=w.ledger_account_id
                WHERE w.client_id=$1 AND (b.balance<>0 OR b.carrying_usdt<>0))
        """, client_id):
            raise InvalidPosting('Сначала погасите все остатки клиента')
        await self._db.execute('UPDATE ledger_accounts SET is_active=FALSE WHERE id IN (SELECT ledger_account_id FROM client_wallet_accounts WHERE client_id=$1)',client_id)
        await self._db.execute('UPDATE clients SET is_active=FALSE WHERE id=$1', client_id)
        await self._db.execute('UPDATE client_chat_links SET is_active=FALSE WHERE client_id=$1', client_id)
        return {'client_id':client_id, 'is_active':False}


    async def lock_transfer(self, operation_id):
        await self._db.execute("SELECT pg_advisory_xact_lock(hashtextextended('client-transfer:'||$1::bigint::text,0))", operation_id)

    async def transfer_context(self, operation_id):
        root_id = await self._db.fetchval("""
            SELECT coalesce((command_details->>'transfer_root_id')::bigint,id)
            FROM financial_operations WHERE id=$1 AND kind='wallet_transfer' AND status='posted'
        """, operation_id)
        if root_id is None:
            raise InvalidPosting('Перевод не найден')
        row = await self._db.fetchrow("""
            SELECT o.*,coalesce((SELECT r.effective_at FROM financial_operations r WHERE r.reversal_of_id=o.id AND r.status='posted'),o.effective_at) AS status_at, EXISTS(SELECT 1 FROM financial_operations r
                WHERE r.reversal_of_id=o.id AND r.status='posted') AS cancelled
            FROM financial_operations o WHERE o.kind='wallet_transfer' AND o.status='posted'
                AND (o.id=$1 OR (o.command_details->>'transfer_root_id')::bigint=$1)
            ORDER BY o.id DESC LIMIT 1
        """, root_id)
        details = json.loads(row['command_details'])
        participants = []
        for key in ('source_client','target_client'):
            participant = await self._db.fetchrow("""
                SELECT c.id,p.name,l.telegram_chat_id AS chat_id FROM clients c
                JOIN parties p ON p.id=c.party_id LEFT JOIN LATERAL (
                    SELECT telegram_chat_id FROM client_chat_links WHERE client_id=c.id AND is_active ORDER BY id LIMIT 1
                ) l ON TRUE WHERE c.id=$1
            """, details[key])
            participants.append(dict(participant))
        return {'root_id':root_id, 'operation_id':row['id'], 'cancelled':row['cancelled'],
            'effective_at':row['status_at'], 'details':details,
            'source':participants[0], 'target':participants[1]}


    async def zero_preview(self, client_id):
        client = await self._db.fetchrow("""
            SELECT c.id,p.name,c.is_active FROM clients c JOIN parties p ON p.id=c.party_id WHERE c.id=$1
        """,client_id)
        if client is None or not client['is_active']:
            raise InvalidPosting('Клиент не найден или закрыт')
        rows = await self._db.fetch("""
            SELECT w.ledger_account_id AS id,w.instrument_code,b.balance,b.carrying_usdt,i.precision,
                (SELECT max(l.operation_id) FROM journal_lines l JOIN financial_operations o ON o.id=l.operation_id
                    WHERE l.account_id=w.ledger_account_id AND o.status='posted') AS last_operation_id
            FROM client_wallet_accounts w JOIN posted_account_balances b ON b.account_id=w.ledger_account_id
            JOIN instruments i ON i.code=w.instrument_code WHERE w.client_id=$1 AND w.kind='client'
            ORDER BY w.ledger_account_id
        """,client_id)
        state = [dict(row) for row in rows]
        fingerprint = hashlib.sha256(canonical_json({'client_id':client_id,'wallets':state}).encode()).hexdigest()
        return {'client_id':client_id,'client_name':client['name'],'wallets':state,'fingerprint':fingerprint}


    async def wallet_adjustment_details(self, operation_id):
        details = await self._db.fetchval("""
            SELECT command_details FROM financial_operations WHERE id=$1 AND status='posted'
                AND kind='manual_journal' AND command_details->>'purpose'='wallet_adjustment'
        """,operation_id)
        if details is None:
            raise InvalidPosting('Это не корректировка кошелька')
        return json.loads(details)
