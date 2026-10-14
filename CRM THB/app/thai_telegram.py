import json
import re
import shlex
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError

from app.thai_russian_commands import (
    INSTRUMENTS,
    RussianTelegramCommands,
    instrument,
)
from domain.finance.models import InvalidPosting
from services.finance.outbox import DeliveryError


class ThaiTelegramCommands:
    def __init__(self, container, config):
        self._app, self._config = container, config
        self._russian = RussianTelegramCommands(container, config)

    @property
    def operations_chat_id(self):
        return self._config.operations_chat_id

    async def deliver_cash_request(self, bot, deal_id):
        await ThaiTelegramDelivery(self._app, bot, self.operations_chat_id).deliver_cash_request(deal_id)

    @property
    def transfer_aliases(self):
        return INSTRUMENTS

    def resolve_instrument(self, value):
        return instrument(value)

    async def deliver_payment_watch(self,bot,watch_id):
        await ThaiTelegramDelivery(self._app,bot,self.operations_chat_id).deliver_payment_watch(watch_id)

    async def deliver_best_change(self,bot,deal_id):
        await ThaiTelegramDelivery(self._app,bot,self.operations_chat_id).deliver_best_change(deal_id)

    async def deliver_wallet_adjustment(self, bot, operation_id):
        await ThaiTelegramDelivery(self._app,bot,self.operations_chat_id).deliver_wallet_adjustment(operation_id)

    async def deliver_client_transfer(self, bot, operation_id):
        await ThaiTelegramDelivery(self._app,bot,self.operations_chat_id).deliver_client_transfer(operation_id)

    async def deliver_exchange_request(self, bot, deal_id):
        await ThaiTelegramDelivery(self._app,bot,self.operations_chat_id).deliver_exchange_request(deal_id)

    async def exchange_by_command(self, chat_id, message_id):
        deal = await self._app.trading_queries.exchange_by_reference(f'tg:{chat_id}:{message_id}')
        if deal is None:
            raise InvalidPosting('Не найдена обменная заявка')
        return deal['id']

    async def edit_exchange(self, *, telegram_user_id, chat_id, message_id, reply_message_id, text, actor_name, effective_at):
        actor = await self.authorize(telegram_user_id)
        deal_id = await self._app.exchange_requests.source_links.accounting_by_message(chat_id,reply_message_id)
        if deal_id is None:
            raise InvalidPosting('Ответьте на оригинальную карточку обменной заявки')
        common = {'key':f'tg:{chat_id}:{message_id}:exchange-edit','actor_id':actor,'effective_at':effective_at,
            'business_date':effective_at.astimezone(ZoneInfo('Asia/Bangkok')).date()}
        return await self._app.exchange_requests.edit_accounting(text=text,deal_id=deal_id,common=common,actor_name=actor_name)

    async def cancel_exchange(self, *, telegram_user_id, chat_id, message_id, data):
        from datetime import UTC, datetime
        actor = await self.authorize(telegram_user_id)
        match = re.fullmatch(r'req_cancel:(\d+):(\d+)',data)
        if match is None:
            raise InvalidPosting('Некорректная кнопка отмены')
        request_id,active_id = map(int,match.groups())
        linked = await self._app.exchange_requests.source_links.accounting_by_message(chat_id,message_id)
        if linked is None or (await self._app.trading_queries.exchange_context(linked))['root']['id']!=request_id:
            raise InvalidPosting('Кнопка не принадлежит этой карточке заявки')
        now = datetime.now(UTC)
        return await self._app.exchange_requests.cancel_accounting(deal_id=request_id,expected_deal_id=active_id,
            common={'key':f'cancel-exchange:{request_id}:{active_id}:{actor}','actor_id':actor,
                'business_date':now.astimezone(ZoneInfo('Asia/Bangkok')).date(),'effective_at':now},
            reason='Отмена обменной заявки в Telegram')

    async def cancel_exchange_reply(self, *, telegram_user_id, chat_id, reply_message_id):
        linked = await self._app.exchange_requests.source_links.accounting_by_message(chat_id,reply_message_id)
        if linked is None:
            return None
        context = await self._app.exchange_requests.source_links.accounting_context(linked)
        return await self.cancel_exchange(telegram_user_id=telegram_user_id,chat_id=chat_id,message_id=reply_message_id,
            data=f"req_cancel:{context['root']['id']}:{context['active']['id']}")

    async def cash_request_by_command(self, chat_id, message_id):
        return await self._app.pool.fetchval(
            "SELECT id FROM deals WHERE external_reference=$1 AND kind IN ('deposit','withdrawal','cash_exchange')",
            f"tg:{chat_id}:{message_id}:cash-request")

    async def cash_request_by_message(self, chat_id, message_id):
        return await self._app.pool.fetchval(
            "SELECT deal_id FROM telegram_message_links WHERE chat_id=$1 AND message_id=$2 AND purpose IN ('cash_client','cash_request')",
            chat_id, message_id)

    async def edit_cash_request(self, *, telegram_user_id, chat_id, message_id, request_id, text, actor_name, effective_at):
        actor = await self.authorize(telegram_user_id)
        parsed = self._app.cash_requests.parse_command(text)
        if parsed is None:
            raise InvalidPosting('Некорректная кассовая команда')
        return await self._app.cash_requests.edit_request(key=f'tg:{chat_id}:{message_id}:cash-edit',
            actor_id=actor, request_id=f'Б-{request_id:06d}', parsed=parsed,
            editor_name=actor_name, effective_at=effective_at)

    async def cash_status(self, *, telegram_user_id, chat_id, message_id, data):
        from services.cash_requests.constants import CB_DEAL_CANCEL, CB_DEAL_DONE, CB_DEAL_READY
        actor = await self.authorize(telegram_user_id)
        action = next((action for prefix,action in ((CB_DEAL_READY,'ready'),(CB_DEAL_CANCEL,'cancel'),(CB_DEAL_DONE,'done')) if data.startswith(prefix)), None)
        if action is None:
            raise InvalidPosting('Неизвестная кнопка')
        prefix = {'ready':CB_DEAL_READY,'cancel':CB_DEAL_CANCEL,'done':CB_DEAL_DONE}[action]
        request_id, revision = data[len(prefix):].rsplit(':',1)
        deal_id = self._app.cash_requests.deal_id(request_id)
        deal = await self._app.trading_queries.detail(deal_id)
        allowed_chat = self.operations_chat_id or deal['attributes'].get('source_chat_id')
        if chat_id != allowed_chat or await self.cash_request_by_message(chat_id,message_id)!=deal_id:
            raise InvalidPosting('Кнопка доступна только в рабочей карточке заявки')
        return await self._app.cash_requests.change_request_status(
            key=f'cash:{deal_id}:{revision}:{action}', actor_id=actor, request_id=request_id,
            expected_revision=int(revision), action=action)

    async def authorize(self, telegram_user_id):
        actor = await self._app.queries.actor_by_telegram_id(telegram_user_id)
        if actor is None or not await self._app.queries.can_read(actor):
            raise InvalidPosting("Нет доступа к Thai CRM")
        return actor

    async def execute(
        self, *, telegram_user_id, chat_id, message_id, text, effective_at, private=False,
        chat_name=None, actor_name=None,
    ):
        words = shlex.split(text)
        command, args = words[0].split("@")[0].lower(), words[1:]
        key = f"tg:{chat_id}:{message_id}"
        response = await self._russian.execute_customer(
            command=command, args=args, key=key, telegram_user_id=telegram_user_id,
            chat_id=chat_id, chat_name=chat_name,
        )
        if response is not None:
            return response
        actor = await self.authorize(telegram_user_id)
        day = effective_at.astimezone(ZoneInfo("Asia/Bangkok")).date()
        common = {"key": key, "actor_id": actor, "business_date": day, "effective_at": effective_at}
        response = await self._russian.execute(
            command=command, args=args, text=text, chat_id=chat_id, common=common,
            chat_name=chat_name, telegram_user_id=telegram_user_id, actor_name=actor_name,
        )
        if response is not None:
            return response
        if command == "/token":
            raise InvalidPosting("Вход в CRM через Telegram: /вход")
        raise InvalidPosting("Неизвестная команда; /помощь")




class ThaiTelegramDelivery:
    def __init__(self, container, bot, operations_chat_id=None):
        self._app, self._bot, self._operations_chat_id = container, bot, operations_chat_id

    async def deliver_exchange_request(self, deal_id, *, event=None):

        from telegram_adapters.exchange_keyboards import AiogramExchangeKeyboardPresenter
        context = await self._app.trading_queries.exchange_context(deal_id)
        root_id = context['root']['id']
        chats = set(await self._app.pool.fetchval(
            'SELECT coalesce(array_agg(telegram_chat_id),ARRAY[]::bigint[]) FROM client_chat_links WHERE client_id=$1 AND is_active',
            context['root']['client_id']))
        source = context['root']['attributes'].get('presentation',{}).get('source_chat_id')
        if source:
            chats.add(source)
        if self._operations_chat_id:
            chats.add(self._operations_chat_id)
        keyboards = AiogramExchangeKeyboardPresenter()
        try:
            for chat_id in sorted(chats):
                async with self._app.pool.acquire() as db, db.transaction():
                    await db.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',f'exchange-card:{root_id}:{chat_id}')
                    if event and await db.fetchval(
                        'SELECT EXISTS(SELECT 1 FROM telegram_deliveries WHERE event_key=$1 AND chat_id=$2)',event.key,chat_id):
                        continue
                    current = await self._app.trading_queries.exchange_context(root_id,connection=db)
                    active = current['active']
                    attrs = active['attributes']
                    accounts = await self._app.trading_queries.accounts(active['client_id'],attrs['receive']['instrument'],
                        attrs['pay']['instrument'],attrs['mode'],connection=db)
                    texts = await self._app.exchange_requests.accounting_texts(current,accounts=accounts)
                    request_card = chat_id==self._operations_chat_id
                    text = texts.request_text if request_card else texts.client_text
                    markup = None if active['effective_status']=='cancelled' else (
                        keyboards.request_chat(request_id=root_id,table_request_id=active['id']) if request_card
                        else keyboards.client_cancel(request_id=root_id,table_request_id=active['id']))
                    link = await db.fetchrow("""
                        SELECT message_id FROM telegram_message_links WHERE deal_id=$1 AND chat_id=$2
                            AND purpose IN ('exchange_client','exchange_request') ORDER BY id LIMIT 1
                    """,root_id,chat_id)
                    if link is None:
                        link = await db.fetchrow("""
                            SELECT t.message_id FROM telegram_deliveries t JOIN telegram_outbox e ON e.event_key=t.event_key
                            WHERE e.operation_id=$1 AND t.chat_id=$2 ORDER BY e.id LIMIT 1
                        """,current['root']['operation_id'],chat_id)
                    message_id = await self._update_card(db,deal_id=root_id,chat_id=chat_id,text=text,
                        markup=markup,purpose='exchange_request' if request_card else 'exchange_client',link=link)
                    if event:
                        await db.execute('INSERT INTO telegram_deliveries VALUES ($1,$2,$3,clock_timestamp()) ON CONFLICT DO NOTHING',
                            event.key,chat_id,message_id)
        except TelegramAPIError:
            raise DeliveryError('exchange_card_delivery_failed') from None

    async def _update_card(self, connection, *, deal_id=None, operation_id=None, chat_id, text, markup, purpose, link, parse_mode="HTML"):
        from aiogram.exceptions import TelegramBadRequest
        if link:
            message_id = link['message_id']
            try:
                await self._bot.edit_message_text(text,chat_id=chat_id,message_id=message_id,
                    parse_mode=parse_mode,reply_markup=markup)
            except TelegramBadRequest as error:
                if 'message is not modified' not in str(error).lower():
                    raise
        else:
            sent = await self._bot.send_message(chat_id,text,parse_mode=parse_mode,reply_markup=markup)
            message_id = sent.message_id
        await connection.execute("""
            INSERT INTO telegram_message_links(deal_id,chat_id,message_id,purpose,operation_id) VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT(chat_id,message_id) DO NOTHING
        """,deal_id,chat_id,message_id,purpose,operation_id)
        return message_id

    async def deliver(self, event):
        payload = json.loads(event.payload_json)
        if event.event_type=='payment.watch_changed':
            await self.deliver_payment_watch(payload['watch_id'])
            return
        if event.event_type=='cash.request_changed':
            await self.deliver_cash_request(payload['deal_id'])
            return
        operation_id = payload["operation_id"]
        row = await self._app.pool.fetchrow(
            "SELECT kind,business_date,command_details FROM financial_operations WHERE id=$1",
            operation_id,
        )
        details = json.loads(row["command_details"])
        if row['kind']=='profit_configure':
            return
        if details.get('payment_watch_id'):
            await self.deliver_payment_watch(details['payment_watch_id'])
            return
        if row['kind']=='best_change':
            deal_id = await self._app.pool.fetchval('SELECT id FROM deals WHERE operation_id=$1',operation_id)
            await self.deliver_best_change(deal_id)
            return
        if row['kind']=='manual_journal' and details.get('purpose')=='wallet_adjustment':
            await self.deliver_wallet_adjustment(operation_id)
            return
        if row["kind"]=="wallet_transfer":
            await self.deliver_client_transfer(operation_id)
            return
        if row['kind']=='reversal' and details.get('original'):
            original = await self._app.pool.fetchval('SELECT command_details FROM financial_operations WHERE id=$1',details['original'])
            original = json.loads(original)
            if await self._app.pool.fetchval('SELECT kind FROM financial_operations WHERE id=$1',details['original'])=='best_change':
                deal_id = await self._app.pool.fetchval('SELECT id FROM deals WHERE operation_id=$1',details['original'])
                await self.deliver_best_change(deal_id)
                return
            if original.get('purpose')=='wallet_adjustment':
                await self.deliver_wallet_adjustment(details['original'])
                return
            if await self._app.pool.fetchval('SELECT kind FROM financial_operations WHERE id=$1',details['original'])=='wallet_transfer':
                await self.deliver_client_transfer(details['original'])
                return
            if not original.get('cash_request_id') and await self._app.pool.fetchval('SELECT kind FROM financial_operations WHERE id=$1',details['original'])=='fx_trade':
                await self.deliver_exchange_request(original['deal_id'],event=event)
                return
            if original.get('request_id'):
                await self.deliver_cash_request(original['request_id'])
                return
            if original.get('cash_request_id'):
                await self.deliver_cash_request(original['cash_request_id'])
                return
        if details.get('cash_request_id'):
            await self.deliver_cash_request(details['cash_request_id'])
            return
        if details.get('request_id') is not None and row['kind'] in ('wallet_deposit','wallet_withdrawal'):
            await self.deliver_cash_request(details['request_id'])
            return
        client_id = details.get("client", details.get("client_id"))
        if client_id is None and details.get("deal_id"):
            client_id = await self._app.pool.fetchval(
                "SELECT client_id FROM deals WHERE id=$1", details["deal_id"]
            )
        if row['kind']=='fx_trade':
            await self.deliver_exchange_request(details['deal_id'],event=event)
            return
        chats = set(
            await self._app.pool.fetchval(
                "SELECT coalesce(array_agg(telegram_chat_id),ARRAY[]::bigint[]) FROM client_chat_links WHERE client_id=$1 AND (is_active OR $2)",
                client_id,details.get("purpose")=="client_zero",
            )
        )
        chats.update(
            row["telegram_chat_id"]
            for row in await self._app.pool.fetch(
                """
            SELECT DISTINCT c.telegram_chat_id FROM journal_lines l JOIN client_wallet_accounts w ON w.ledger_account_id=l.account_id
                JOIN client_chat_links c ON c.client_id=w.client_id AND c.is_active WHERE l.operation_id=$1
        """,
                operation_id,
            )
        )
        if self._operations_chat_id:
            chats.add(self._operations_chat_id)
        try:
            for chat_id in sorted(chats):
                async with self._app.pool.acquire() as connection, connection.transaction():
                    # Serialize immediate sends and worker retries for this event/chat.
                    await connection.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                        f"telegram:{event.key}:{chat_id}",
                    )
                    if await connection.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM telegram_deliveries WHERE event_key=$1 AND chat_id=$2)",
                        event.key, chat_id,
                    ):
                        continue
                    sent = await self._bot.send_message(
                        chat_id,
                        (f"Клиент обнулён и закрыт. Корректировки: операция #{operation_id}.\nИстория сохранена."
                         if details.get('purpose')=='client_zero' else
                         f"Thai CRM · #{operation_id} · {row['kind']} · {row['business_date']}\nУчёт себестоимости и прибыли: USDT"),
                    )
                    await connection.execute(
                        "INSERT INTO telegram_deliveries VALUES ($1,$2,$3,clock_timestamp()) ON CONFLICT DO NOTHING",
                        event.key, chat_id, sent.message_id,
                    )
        except TelegramAPIError:
            raise DeliveryError("telegram_delivery_failed") from None


    async def deliver_cash_request(self, deal_id):

        from telegram_adapters.cash_keyboards import AiogramCashKeyboardPresenter
        deal = await self._app.trading_queries.detail(deal_id)
        attrs = deal['attributes']
        chats = set(await self._app.pool.fetchval(
            "SELECT coalesce(array_agg(telegram_chat_id),ARRAY[]::bigint[]) FROM client_chat_links WHERE client_id=$1 AND is_active",
            deal['client_id']))
        if attrs.get('source_chat_id'):
            chats.add(attrs['source_chat_id'])
        operations_chat = self._operations_chat_id or attrs.get('source_chat_id')
        if operations_chat:
            chats.add(operations_chat)
        try:
            for chat_id in sorted(chats):
                async with self._app.pool.acquire() as connection, connection.transaction():
                    await connection.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', f'cash-card:{deal_id}:{chat_id}')
                    # Re-read under the delivery lock: old events cannot revert a newer card.
                    current = await self._app.trading_queries.detail(deal_id, connection=connection)
                    plan = self._app.cash_requests.card_plan(current, AiogramCashKeyboardPresenter())
                    request_card = chat_id==operations_chat
                    text = plan.request_text if request_card else plan.client_text
                    markup = plan.request_markup if request_card else None
                    link = await connection.fetchrow(
                        "SELECT * FROM telegram_message_links WHERE deal_id=$1 AND chat_id=$2 AND purpose IN ('cash_client','cash_request') ORDER BY id LIMIT 1",
                        deal_id, chat_id)
                    await self._update_card(connection,deal_id=deal_id,chat_id=chat_id,text=text,
                        markup=markup,purpose='cash_request' if request_card else 'cash_client',link=link)
        except TelegramAPIError:
            raise DeliveryError('cash_card_delivery_failed') from None


    async def deliver_client_transfer(self, operation_id):
        from aiogram.types import BufferedInputFile

        from db_asyncpg.thai_clients.repository import ClientsRepository

        initial = await self._app.client_queries.transfer_context(operation_id)
        root = initial['root_id']
        try:
            for side in ('source','target'):
                for purpose in ('receipt','text'):
                    # Each successful send commits independently; a failed later send is retried alone.
                    async with self._app.pool.acquire() as db, db.transaction():
                        await db.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',f'transfer-delivery:{root}')
                        context = await ClientsRepository(db).transfer_context(root)
                        participant = context[side]
                        chat_id = participant['chat_id']
                        if chat_id is None:
                            continue
                        link_purpose = f"client_transfer_{purpose}_{'cancelled' if context['cancelled'] else 'posted'}"
                        if await db.fetchval('SELECT EXISTS(SELECT 1 FROM telegram_message_links WHERE operation_id=$1 AND chat_id=$2 AND purpose=$3)',context['operation_id'],chat_id,link_purpose):
                            continue
                        instrument = context['details']['instrument']
                        if purpose=='receipt':
                            receipt,caption,_ = self._app.client_transfers.accounting_receipt(context)
                            sent = await self._bot.send_photo(chat_id,BufferedInputFile(receipt,filename=f'transfer_{root}.png'),caption=caption)
                        else:
                            wallets = await self._app.client_queries.wallets(participant['id'],connection=db)
                            balance = next(w['balance'] for w in wallets if w['instrument_code']==instrument)
                            text = self._app.client_transfers.accounting_notice(context,side,balance)
                            sent = await self._bot.send_message(chat_id,text,parse_mode=None)
                        await db.execute('INSERT INTO telegram_message_links(operation_id,chat_id,message_id,purpose) VALUES ($1,$2,$3,$4)',context['operation_id'],chat_id,sent.message_id,link_purpose)
        except TelegramAPIError:
            raise DeliveryError('client_transfer_delivery_failed') from None


    async def deliver_wallet_adjustment(self, operation_id):
        from telegram_adapters.wallet_keyboards import AiogramWalletKeyboardPresenter
        original = await self._app.queries.detail(operation_id)
        details = original['command_details']
        if original['kind']!='manual_journal' or details.get('purpose')!='wallet_adjustment':
            raise InvalidPosting('Это не корректировка кошелька')
        chats = await self._app.pool.fetch('SELECT telegram_chat_id FROM client_chat_links WHERE client_id=$1 AND is_active',details['client'])
        try:
            for chat in chats:
                chat_id = chat['telegram_chat_id']
                async with self._app.pool.acquire() as db, db.transaction():
                    await db.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',f'wallet-card:{operation_id}:{chat_id}')
                    cancelled = await db.fetchval("SELECT EXISTS(SELECT 1 FROM financial_operations WHERE reversal_of_id=$1 AND status='posted')",operation_id)
                    wallets = await self._app.client_queries.wallets(details['client'],connection=db)
                    balance = next(row['balance'] for row in wallets if row['instrument_code']==details['instrument'])
                    text = self._app.wallet_interactions.accounting_response(details,balance)
                    if cancelled:
                        text+='\n↩️ Отменено.'
                    markup = None if cancelled else AiogramWalletKeyboardPresenter().undo_operation(operation_id)
                    link = await db.fetchrow("SELECT * FROM telegram_message_links WHERE operation_id=$1 AND chat_id=$2 AND purpose='wallet_adjustment'",operation_id,chat_id)
                    await self._update_card(db,operation_id=operation_id,chat_id=chat_id,text=text,markup=markup,
                        purpose='wallet_adjustment',link=link,parse_mode=None)
        except TelegramAPIError:
            raise DeliveryError('wallet_adjustment_delivery_failed') from None


    async def deliver_best_change(self,deal_id):
        from db_asyncpg.thai_profit.repository import ProfitRepository
        from handlers.best_change import format_best_change_result
        context = await self._app.profit_queries.best_change_context(deal_id)
        root = context['root_id']
        reference = await self._app.pool.fetchval('SELECT external_reference FROM deals WHERE id=$1',root)
        match = re.fullmatch(r'tg:(-?\d+):\d+',reference or '')
        source_chat = int(match.group(1)) if match else None
        chats = set(await self._app.pool.fetchval(
            "SELECT coalesce(array_agg(telegram_chat_id),ARRAY[]::bigint[]) FROM client_chat_links WHERE client_id=$1 AND is_active",
            context['active']['client_id']))
        bindings = await self._app.profit_queries.bindings()
        buffer_chats = set(await self._app.pool.fetchval(
            "SELECT coalesce(array_agg(telegram_chat_id),ARRAY[]::bigint[]) FROM client_chat_links WHERE client_id=$1 AND is_active",
            bindings['best_change_buffer']['client_id']))
        chats.update(buffer_chats)
        if source_chat:
            chats.add(source_chat)
        if self._operations_chat_id:
            chats.add(self._operations_chat_id)
        try:
            for chat_id in sorted(chats):
                async with self._app.pool.acquire() as db,db.transaction():
                    await db.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',f'best-change-card:{root}:{chat_id}')
                    current = await ProfitRepository(db).best_change_context(root)
                    link = await db.fetchrow("SELECT * FROM telegram_message_links WHERE deal_id=$1 AND chat_id=$2 AND purpose='best_change'",root,chat_id)
                    await self._update_card(db,deal_id=root,chat_id=chat_id,
                        text=format_best_change_result(current,include_internal=chat_id in buffer_chats or chat_id in (source_chat,self._operations_chat_id)),
                        markup=None,purpose='best_change',link=link,parse_mode=None)
        except TelegramAPIError:
            raise DeliveryError('best_change_delivery_failed') from None

    async def deliver_payment_watch(self,watch_id):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        watch = await self._app.trading_queries.payment_watch(watch_id)
        try:
            async with self._app.pool.acquire() as db,db.transaction():
                await db.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',f'payment-watch-card:{watch_id}')
                watch = await self._app.trading_queries.payment_watch(watch_id,connection=db)
                markup = None
                actions = ('continue','stop') if watch['status']=='TIMED_OUT' else ('stop',) if watch['status']=='WATCHING' else ()
                if actions:
                    markup = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text='Продолжить' if action=='continue' else 'Остановить',
                            callback_data=f"paywatch:{action}:{watch_id}:{watch['continue_count']}") for action in actions]])
                text = self._app.payment_watches.builder.build_accounting(watch)
                if watch['notice_message_id']:
                    from aiogram.exceptions import TelegramBadRequest
                    try:
                        await self._bot.edit_message_text(text,chat_id=watch['chat_id'],message_id=watch['notice_message_id'],
                            parse_mode='HTML',reply_markup=markup)
                    except TelegramBadRequest as error:
                        if 'message is not modified' not in str(error).lower():
                            raise
                else:
                    sent = await self._bot.send_message(watch['chat_id'],text,parse_mode='HTML',reply_markup=markup)
                    await db.execute('UPDATE payment_watches SET notice_message_id=$2 WHERE id=$1',watch_id,sent.message_id)
        except TelegramAPIError:
            raise DeliveryError('payment_watch_delivery_failed') from None
        if watch['status']=='COMPLETED' and watch['client_id'] is not None:
            await self.deliver_payment_receipt(watch_id)

    async def deliver_payment_receipt(self,watch_id):
        from aiogram.types import BufferedInputFile
        watch = await self._app.trading_queries.payment_watch(watch_id)
        main = next(event for event in watch['events'] if event['event_type']=='MAIN')
        try:
            for purpose in ('payment_receipt','payment_balance'):
                async with self._app.pool.acquire() as db,db.transaction():
                    await db.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',f'payment-receipt:{watch_id}')
                    if await db.fetchval('SELECT EXISTS(SELECT 1 FROM telegram_message_links WHERE operation_id=$1 AND chat_id=$2 AND purpose=$3)',
                        main['operation_id'],watch['chat_id'],purpose):
                        continue
                    if purpose=='payment_receipt':
                        blob = self._app.payment_watches.receipt_builder.build_main_success(amount=main['amount'],recipient_address=watch['address'],
                            tx_hash=main['tx_hash'],block_ts=main['block_ts'],timezone='Asia/Bangkok',precision=6)
                        sent = await self._bot.send_photo(watch['chat_id'],BufferedInputFile(blob,filename=f"payment_receipt_{watch_id}.png"),
                            caption=self._app.payment_watches.builder.build_main_success(amount=main['amount'],tx_hash=main['tx_hash'],direction='IN'),parse_mode='HTML')
                    else:
                        wallets = await self._app.client_queries.wallets(watch['client_id'],connection=db)
                        balance = next(row['balance'] for row in wallets if row['instrument_code']=='USDT')
                        text = self._app.wallet_interactions.accounting_response({'quantity':-main['amount'],'instrument':'USDT'},balance)
                        sent = await self._bot.send_message(watch['chat_id'],text,parse_mode=None)
                    await db.execute('INSERT INTO telegram_message_links(operation_id,chat_id,message_id,purpose) VALUES ($1,$2,$3,$4)',
                        main['operation_id'],watch['chat_id'],sent.message_id,purpose)
        except TelegramAPIError:
            raise DeliveryError('payment_receipt_delivery_failed') from None
