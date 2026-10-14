import json
from decimal import Decimal

import asyncpg

from domain.finance.models import FxQuote, InvalidPosting, ReversalBlocked, canonical_json
from services.exchange.ledger import MissingMarketQuote


class TradingRepository:
    def __init__(self, connection: asyncpg.Connection):
        self._db = connection

    async def create(self, actor_id, client_id, attributes, external_reference, kind="fx_trade"):
        deal_id = await self._db.fetchval(
            """
            INSERT INTO deals(client_id,kind,source,status,external_reference,created_by,attributes)
            VALUES ($1,$5,'thai','draft',$2,$3,$4::jsonb) RETURNING id
        """,
            client_id,
            external_reference,
            actor_id,
            canonical_json(attributes),
            kind,
        )
        if client_id is not None:
            await self._db.execute(
                "INSERT INTO exchange_requests(external_reference,client_id,deal_id,status,created_by) VALUES ($1,$2,$3,'draft',$4)",
                external_reference,
                client_id,
                deal_id,
                actor_id,
            )
        return {"deal_id": deal_id, "status": "draft"}

    async def replace_cash_request(self, deal_id, attributes, expected_revision):
        deal = await self.lock(deal_id)
        if deal['kind'] not in ('deposit', 'withdrawal','cash_exchange') or deal['status'] != 'draft':
            raise InvalidPosting('Редактировать можно только новую кассовую заявку')
        if deal['attributes']['revision'] != expected_revision:
            raise InvalidPosting('Заявка уже изменена; обновите карточку')
        await self._db.execute('UPDATE deals SET attributes=$2::jsonb WHERE id=$1', deal_id, canonical_json(attributes))

    async def cash_request_changed(self, deal_id):
        deal = await self.lock(deal_id)
        if deal['status'] in ('ready','cancelled') and deal['operation_id'] is None:
            await self._db.execute("""
                UPDATE deals SET attributes=attributes || jsonb_build_object('status_at',clock_timestamp(),'status_value',$2::text)
                WHERE id=$1 AND (attributes->>'status_value') IS DISTINCT FROM $2
            """, deal_id, deal['status'])
        confirmed = len(await self.cash_confirmations(deal_id))
        key = f"cash:request:{deal_id}:{deal['status']}:{deal['attributes']['revision']}:{confirmed}"
        await self._db.execute("""
            INSERT INTO telegram_outbox(event_key,event_type,payload)
            VALUES ($1,'cash.request_changed',$2::jsonb) ON CONFLICT(event_key) DO NOTHING
        """, key, canonical_json({'deal_id': deal_id}))

    async def cash_confirmations(self, deal_id):
        return [dict(row) for row in await self._db.fetch(
            'SELECT * FROM cash_exchange_confirmations WHERE deal_id=$1 ORDER BY direction',deal_id)]

    async def confirm_cash_exchange(self, deal_id, direction, instrument, quantity, quoted_rate, actor_id, confirmed_at):
        deal = await self.lock(deal_id)
        leg = deal['attributes'][direction]
        if (deal['kind']!='cash_exchange' or deal['status']!='ready' or leg['instrument']!=instrument
            or Decimal(leg['quantity'])!=quantity):
            raise InvalidPosting('Подтверждение не соответствует кассовой заявке')
        previous = await self._db.fetchrow('SELECT * FROM cash_exchange_confirmations WHERE deal_id=$1 AND direction=$2',deal_id,direction)
        if previous is not None:
            if quoted_rate is not None and Decimal(str(quoted_rate))!=previous['quoted_rate']:
                raise InvalidPosting('Сторона уже подтверждена с другим курсом')
            return
        await self._db.execute("""
            INSERT INTO cash_exchange_confirmations(deal_id,direction,instrument_code,quantity,quoted_rate,actor_id,confirmed_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
        """,deal_id,direction,instrument,quantity,None if quoted_rate is None else Decimal(str(quoted_rate)),actor_id,confirmed_at)

    async def complete_cash_request(self, deal_id, operation_id):
        deal = await self.lock(deal_id)
        if deal['kind'] not in ('deposit', 'withdrawal') or deal['status'] != 'ready':
            raise InvalidPosting('Заявка не готова к расчёту')
        await self._db.execute("UPDATE deals SET status='posted',operation_id=$2 WHERE id=$1", deal_id, operation_id)
        await self._db.execute("UPDATE exchange_requests SET status='posted' WHERE deal_id=$1", deal_id)
        await self._db.execute("""
            INSERT INTO deal_legs(deal_id,number,direction,instrument_code,quantity)
            VALUES ($1,1,$2,$3,$4)
        """, deal_id, 'receive' if deal['kind']=='deposit' else 'pay',
            deal['attributes']['instrument'], Decimal(deal['attributes']['quantity']))

    async def lock(self, deal_id):
        row = await self._db.fetchrow("SELECT * FROM deals WHERE id=$1 FOR UPDATE", deal_id)
        if row is None:
            raise InvalidPosting("Сделка не найдена")
        result = dict(row)
        result["attributes"] = json.loads(row["attributes"])
        if await self._db.fetchval(
            "SELECT EXISTS(SELECT 1 FROM deal_reversals WHERE deal_id=$1)", deal_id
        ):
            result["status"] = "cancelled"
        return result

    async def ready(self, deal_id):
        deal = await self.lock(deal_id)
        if deal["status"] not in ("draft", "ready"):
            raise InvalidPosting("Подтвердить можно только черновик")
        await self._db.execute("UPDATE deals SET status='ready' WHERE id=$1", deal_id)
        await self._db.execute(
            "UPDATE exchange_requests SET status='ready' WHERE deal_id=$1", deal_id
        )
        return {"deal_id": deal_id, "status": "ready"}

    async def exchange_current(self, root_id):
        row = await self._db.fetchrow("""
            WITH RECURSIVE chain AS (
                SELECT id,0 AS revision FROM deals WHERE id=$1
                UNION ALL SELECT r.replacement_deal_id,c.revision+1 FROM chain c
                    JOIN deal_reversals r ON r.deal_id=c.id WHERE r.replacement_deal_id IS NOT NULL
            ) SELECT id FROM chain ORDER BY revision DESC LIMIT 1
        """, root_id)
        if row is None:
            raise InvalidPosting('Заявка не найдена')
        return await self.lock(row['id'])

    async def cancel_draft(self, deal_id, reason):
        deal = await self.lock(deal_id)
        if deal["status"] not in ("draft", "ready"):
            raise InvalidPosting("Проведённая сделка отменяется только сторно")
        await self._db.execute("UPDATE deals SET status='cancelled' WHERE id=$1", deal_id)
        await self._db.execute(
            "UPDATE exchange_requests SET status='cancelled' WHERE deal_id=$1", deal_id
        )
        await self._db.execute(
            "UPDATE payment_watches SET status='STOPPED',stopped_at=clock_timestamp() WHERE deal_id=$1 AND status IN ('WATCHING','TIMED_OUT')",
            deal_id,
        )
        return {"deal_id": deal_id, "status": "cancelled", "reason": reason}

    async def post(self, deal_id, operation_id, attributes):
        await self._db.execute(
            "UPDATE deals SET status='posted',operation_id=$2,attributes=$3::jsonb WHERE id=$1",
            deal_id,
            operation_id,
            canonical_json(attributes),
        )
        for number, direction in enumerate(("receive", "pay"), 1):
            leg = attributes[direction]
            currency = await self._db.fetchval(
                "SELECT currency_code FROM instruments WHERE code=$1", leg["instrument"]
            )
            operation = await self._db.fetchrow(
                "SELECT effective_at,created_by FROM financial_operations WHERE id=$1", operation_id
            )
            rate_id = await self._db.fetchval(
                """
                INSERT INTO fx_rate_snapshots(currency_code,instrument_code,quoted_direction,quoted_rate,usdt_per_unit,effective_at,created_by)
                VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
            """,
                currency,
                leg["instrument"],
                leg["direction"],
                leg["quoted_rate"],
                leg["usdt_per_unit"],
                operation["effective_at"],
                operation["created_by"],
            )
            await self._db.execute(
                "INSERT INTO deal_legs(deal_id,number,direction,instrument_code,quantity,rate_snapshot_id) VALUES ($1,$2,$3,$4,$5,$6)",
                deal_id,
                number,
                direction,
                leg["instrument"],
                leg["quantity"],
                rate_id,
            )
        await self._db.execute(
            "UPDATE exchange_requests SET status='posted' WHERE deal_id=$1", deal_id
        )

    async def quote(self, instrument, effective_at):
        row = await self._db.fetchrow(
            """
            SELECT r.* FROM market_quotes q JOIN fx_rate_snapshots r ON r.id=q.rate_snapshot_id
                JOIN financial_operations o ON o.id=q.operation_id
            WHERE q.instrument_code=$1 AND r.effective_at<=$2 AND o.status='posted'
            ORDER BY r.effective_at DESC,q.id DESC LIMIT 1
        """,
            instrument,
            effective_at,
        )
        if row is None:
            raise MissingMarketQuote(f"Нет курса {instrument}: укажите вручную или сохраните курс в БД")
        return FxQuote(
            row["currency_code"], row["quoted_rate"], row["quoted_direction"], instrument
        )

    async def set_quote(self, operation_id, instrument, quote, actor_id, effective_at):
        rate_id = await self._db.fetchval(
            """
            INSERT INTO fx_rate_snapshots(currency_code,instrument_code,quoted_direction,quoted_rate,usdt_per_unit,effective_at,created_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
        """,
            quote.currency,
            instrument,
            quote.direction,
            quote.quoted_rate,
            quote.usdt_per_unit,
            effective_at,
            actor_id,
        )
        await self._db.execute(
            "INSERT INTO market_quotes(instrument_code,rate_snapshot_id,operation_id) VALUES ($1,$2,$3)",
            instrument,
            rate_id,
            operation_id,
        )

    async def cancelled(self, deal_id, operation_id, reason, replacement):
        await self._db.execute(
            "INSERT INTO deal_reversals VALUES ($1,$2,$3,$4)",
            deal_id,
            operation_id,
            reason,
            replacement,
        )
        await self._db.execute(
            "UPDATE exchange_requests SET status='cancelled' WHERE deal_id=$1", deal_id
        )

    async def watch(self, deal_id, actor_id, chat_id, address, our_address, timeout_at):
        if (
            timeout_at.tzinfo is None
            or not address.strip()
            or not our_address.strip()
            or address == our_address
        ):
            raise InvalidPosting("Нужны разные непустые адреса и время с часовым поясом")
        deal = await self.lock(deal_id)
        if (
            deal["status"] != "ready"
            or deal["attributes"]["mode"] != "cash"
            or deal["attributes"]["pay"]["instrument"] != "USDT"
        ):
            raise InvalidPosting("Мониторинг связывается с готовой кассовой сделкой с выдачей USDT")
        if await self._db.fetchval(
            "SELECT EXISTS(SELECT 1 FROM payment_watches WHERE deal_id=$1 AND status IN ('WATCHING','TIMED_OUT'))",
            deal_id,
        ):
            raise InvalidPosting("Мониторинг уже существует")
        watch_id = await self._db.fetchval(
            """
            INSERT INTO payment_watches(deal_id,chat_id,reply_message_id,address,our_address,created_by_user_id,mode,phase,status,timeout_at)
            VALUES ($1,$2,0,$3,$4,$5,'SINGLE','MAIN','WATCHING',$6) RETURNING id
        """,
            deal_id,
            chat_id,
            address,
            our_address,
            actor_id,
            timeout_at,
        )
        return {"watch_id": watch_id, "deal_id": deal_id, "status": "WATCHING"}

    async def guard_watch(self, deal_id, fact):
        if fact is None and await self._db.fetchval('SELECT EXISTS(SELECT 1 FROM payment_watches WHERE deal_id=$1)',deal_id):
            raise InvalidPosting('Сделка ожидает подтверждения USDT: используйте settlement')
        watches = await self._db.fetch(
            "SELECT * FROM payment_watches WHERE deal_id=$1 AND status IN ('WATCHING','TIMED_OUT') ORDER BY id FOR UPDATE",
            deal_id,
        )
        if watches and fact is None:
            raise InvalidPosting("Сделка ожидает подтверждения USDT: используйте settlement")
        if fact is None:
            return
        if len(watches) != 1 or watches[0]["id"] != fact["watch_id"]:
            raise InvalidPosting("Нет активного мониторинга для этого подтверждения")
        watch = watches[0]
        if (
            fact["block_ts"] < watch["started_at"]
            or fact["confirmations"] < 2
            or fact["token_symbol"] != "USDT"
            or fact["direction"] != "OUT"
            or fact["address"] != watch["address"]
            or fact["our_address"] != watch["our_address"]
        ):
            raise InvalidPosting(
                "Подтверждение не соответствует адресу, USDT или направлению выдачи"
            )

    async def settlement(self, deal_id, operation_id, fact):
        await self._db.execute(
            """
            INSERT INTO payment_watch_events(watch_id,tx_hash,event_type,direction,amount,token_symbol,confirmations,block_ts,operation_id)
            VALUES ($1,$2,'MAIN','OUT',$3,'USDT',$4,$5,$6)
        """,
            fact["watch_id"],
            fact["tx_hash"],
            fact["amount"],
            fact["confirmations"],
            fact["block_ts"],
            operation_id,
        )
        await self._db.execute(
            "UPDATE payment_watches SET status='COMPLETED',completed_at=clock_timestamp() WHERE id=$1",
            fact["watch_id"],
        )

    async def reversible(self, deal_id):
        if await self._db.fetchval(
            "SELECT EXISTS(SELECT 1 FROM payment_watches w JOIN payment_watch_events e ON e.watch_id=w.id WHERE w.deal_id=$1)",
            deal_id,
        ):
            raise ReversalBlocked(
                "Подтверждённый сетевой перевод требует фактического возврата денег"
            )


    async def start_payment_watch(self, *, actor_id, client_id, chat_id, reply_message_id,
                                  address, our_address, quantity, test_mode, timeout_at):
        await self._db.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',f'payment-addresses:{our_address}:{address}')
        if await self._db.fetchval("SELECT EXISTS(SELECT 1 FROM payment_watches WHERE our_address=$1 AND address=$2 AND status IN ('WATCHING','TIMED_OUT'))",our_address,address):
            raise InvalidPosting('Для этих кошельков уже запущено ожидание. Завершите или остановите его.')
        identity = await self._db.fetchval("""
            INSERT INTO payment_watches(client_id,chat_id,reply_message_id,address,our_address,
                created_by_user_id,expected_quantity,mode,phase,status,timeout_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'WATCHING',$10) RETURNING id
        """,client_id,chat_id,reply_message_id,address,our_address,actor_id,quantity,
            'TEST_THEN_MAIN' if test_mode else 'SINGLE','TEST' if test_mode else 'MAIN',timeout_at)
        await self.payment_watch_changed(identity)
        return {'watch_id':identity,'client_id':client_id}

    async def lock_payment_watch(self, watch_id):
        row = await self._db.fetchrow('SELECT * FROM payment_watches WHERE id=$1 FOR UPDATE',watch_id)
        if row is None:
            raise InvalidPosting('Наблюдение не найдено')
        return dict(row)

    async def payment_watch_changed(self, watch_id):
        row = await self.lock_payment_watch(watch_id)
        key = f"payment-watch:{watch_id}:{row['status']}:{row['phase']}:{row['continue_count']}"
        await self._db.execute("""
            INSERT INTO telegram_outbox(event_key,event_type,payload)
            VALUES ($1,'payment.watch_changed',$2::jsonb) ON CONFLICT(event_key) DO NOTHING
        """,key,canonical_json({'watch_id':watch_id}))

    async def change_payment_watch(self, watch_id, action, timeout_at):
        row = await self.lock_payment_watch(watch_id)
        if action=='continue':
            if row['status']!='TIMED_OUT':
                raise InvalidPosting('Продление доступно только после таймаута')
            await self._db.execute("UPDATE payment_watches SET status='WATCHING',timeout_at=$2,continue_count=continue_count+1 WHERE id=$1",watch_id,timeout_at)
        elif action=='stop':
            if row['status'] not in ('WATCHING','TIMED_OUT'):
                raise InvalidPosting('Наблюдение уже завершено или остановлено')
            await self._db.execute("UPDATE payment_watches SET status='STOPPED',stopped_at=clock_timestamp() WHERE id=$1",watch_id)
        elif action=='timeout':
            if row['status']!='WATCHING':
                return
            await self._db.execute("UPDATE payment_watches SET status='TIMED_OUT',timed_out_at=clock_timestamp() WHERE id=$1",watch_id)
        await self.payment_watch_changed(watch_id)

    async def confirmed_payment(self, watch_id, operation_id, transfer, phase, review):
        await self._db.execute("""
            INSERT INTO payment_watch_events(watch_id,tx_hash,event_type,direction,amount,token_symbol,confirmations,block_ts,operation_id)
            VALUES ($1,$2,$3,'OUT',$4,'USDT',$5,$6,$7)
        """,watch_id,transfer.tx_hash,phase,transfer.amount,transfer.confirmations,transfer.block_ts,operation_id)
        await self._db.execute("""
            UPDATE payment_watches SET phase='MAIN',status=$2,review_required=$3,
                completed_at=CASE WHEN $2='COMPLETED' THEN clock_timestamp() ELSE NULL END WHERE id=$1
        """,watch_id,'WATCHING' if phase=='TEST' else 'COMPLETED',review)
        await self.payment_watch_changed(watch_id)

    async def payment_hash_exists(self, tx_hash):
        return await self._db.fetchval('SELECT EXISTS(SELECT 1 FROM payment_watch_events WHERE tx_hash=$1)',tx_hash)


    async def set_payment_wallet(self, address):
        await self._db.execute("INSERT INTO crm_settings(key,value) VALUES ('usdt_wallet',to_jsonb($1::text)) ON CONFLICT(key) DO UPDATE SET value=excluded.value",address)
