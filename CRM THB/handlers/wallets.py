from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from aiogram import F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from services.crm.client_transfer_service import ClientTransferService
from services.expression_calculator import CalcError, evaluate
from services.number_formatting import format_amount_core
from services.wallets.text_builder import WalletTextBuilder
from telegram_adapters.message_context import get_chat_name
from telegram_adapters.statements import handle_stmt_callback

_RE_CASH_REQUEST_ID = re.compile(r"(?iu)^Б-\d+$")


class WalletsHandler:
    @staticmethod
    def _parse_transfer(raw_text: str, *, aliases: Mapping[str, str]) -> tuple[Decimal, str, str]:
        words = shlex.split(raw_text)
        if len(words) < 3:
            raise ValueError(
                "Использование: /перевод 1000 Имя чата или /перевод 100 USDT Имя чата"
            )
        expression = words[1]
        if len(expression) > 200:
            raise ValueError("Слишком длинная сумма перевода")
        try:
            amount = evaluate(expression)
        except (CalcError, InvalidOperation):
            raise ValueError("Некорректная сумма перевода") from None
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Сумма перевода должна быть больше нуля")
        tail = " ".join(words[2:]).strip()
        first, _, remainder = tail.partition(" ")
        instrument = aliases.get(first.lower())
        if instrument is not None and remainder.strip():
            return amount, instrument, remainder.strip()
        return amount, "THB_CASH", tail

    @staticmethod
    def register_accounting(router, commands):
        WalletsHandler.register_accounting_wallets(router,commands)
        async def callback(cq):
            from domain.finance.models import FinanceError, InvalidPosting
            from services.finance.outbox import DeliveryError
            if not isinstance(cq.message,Message):
                await cq.answer('Сообщение недоступно',show_alert=True)
                return
            try:
                phase,source_id,recipient_id = (cq.data or '')[3:].split(':')
                if phase not in ('pick','confirm','reject'):
                    raise InvalidPosting('Некорректное подтверждение')
                actor = await commands.authorize(cq.from_user.id)
                key = ClientTransferService.request_key(cq.message.chat.id,int(source_id))
                request = await commands._app.client_queries.transfer_request(key)
                if phase=='pick':
                    await WalletsHandler._accounting_confirmation(cq.message,request,int(source_id),int(recipient_id))
                else:
                    from datetime import UTC, datetime
                    from zoneinfo import ZoneInfo
                    now = datetime.now(UTC)
                    result = await commands._app.client_transfers.resolve_accounting(key=key,actor_id=actor,
                        recipient_id=int(recipient_id),confirmed=phase=='confirm',effective_at=now,
                        business_date=now.astimezone(ZoneInfo('Asia/Bangkok')).date())
                    if result['operation_id']:
                        await commands.deliver_client_transfer(cq.bot,result['operation_id'])
                    await cq.message.edit_text('Перевод отклонён' if result['cancelled'] else 'Перевод проведён')
                await cq.answer()
            except (FinanceError,ValueError) as error:
                await cq.answer(str(error)[:190],show_alert=True)
            except DeliveryError:
                await cq.answer('Перевод сохранён. Чеки будут отправлены повторно.',show_alert=True)
        router.callback_query.register(callback,F.data.startswith('ct:'))

    @staticmethod
    async def _accounting_confirmation(message, request, source_id, recipient_id):
        from domain.finance.models import InvalidPosting
        from services.wallets.compact_formatter import label_for
        recipient = next((r for r in request['candidates'] if r['id']==recipient_id),None)
        if recipient is None:
            raise InvalidPosting('Получатель не принадлежит этой заявке')
        precision = 6 if request['instrument']=='USDT' else 2
        amount = format_amount_core(Decimal(request['quantity']),precision)
        await message.answer(f"Перевести {amount} {label_for(request['instrument'])}?\nПолучатель: {recipient['name']} · {recipient['chat_id']}",
            parse_mode=None,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text='Подтвердить перевод',callback_data=f'ct:confirm:{source_id}:{recipient_id}'),
                InlineKeyboardButton(text='Отклонить',callback_data=f'ct:reject:{source_id}:{recipient_id}')]]))

    @staticmethod
    async def accounting_transfer(commands, message, *, cancel=False):
        from zoneinfo import ZoneInfo

        from domain.finance.models import FinanceError, InvalidPosting
        from services.finance.outbox import DeliveryError
        if getattr(message,'forward_origin',None) or getattr(message,'forward_from',None):
            return True
        reply = message.reply_to_message
        operation_id = None
        if reply and reply.from_user and reply.from_user.id==message.bot.id:
            operation_id = await commands._app.client_queries.transfer_by_message(message.chat.id,reply.message_id)
        if cancel and operation_id is None:
            return False
        try:
            actor = await commands.authorize(message.from_user.id)
            if reply and operation_id is None:
                raise InvalidPosting('Ответьте на оригинальный чек перевода этого бота')
            common = {'actor_id':actor,'effective_at':message.date,
                      'business_date':message.date.astimezone(ZoneInfo('Asia/Bangkok')).date()}
            if operation_id is not None:
                parts = (message.text or '').split()
                if len(parts)!=(1 if cancel else 2):
                    raise InvalidPosting('Ответьте на чек: /перевод новая_сумма или /отмена')
                result = await commands._app.client_transfers.adjust_accounting(
                    key=f'tg:{message.chat.id}:{message.message_id}:transfer-adjust',operation_id=operation_id,
                    new_amount=None if cancel else parts[1].replace(',','.'),source_chat_id=message.chat.id,**common)
                await commands.deliver_client_transfer(message.bot,result['root_id'])
                await message.answer('Перевод отменён' if result['cancelled'] else 'Перевод изменён')
                return True
            amount,currency,name = WalletsHandler._parse_transfer(
                message.text or '', aliases=commands.transfer_aliases)
            source = await commands._app.trading_queries.client_by_chat(message.chat.id)
            if source is None or message.chat.id==commands.operations_chat_id:
                raise InvalidPosting('Перевод доступен только из клиентского чата')
            request = await commands._app.client_transfers.prepare_accounting(
                key=ClientTransferService.request_key(message.chat.id,message.message_id),actor_id=actor,
                source_client_id=source,source_chat_id=message.chat.id,quantity=amount,instrument=currency,
                recipient_name=name,effective_at=message.date)
            candidates = request['candidates']
            if len(candidates)==1:
                await WalletsHandler._accounting_confirmation(message,request,message.message_id,candidates[0]['id'])
            else:
                await message.answer('Есть несколько чатов с таким названием. Выберите получателя:',
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text=f"{r['name']} · {r['chat_id']}",
                            callback_data=f"ct:pick:{message.message_id}:{r['id']}")]
                        for r in candidates]))
        except (FinanceError,ValueError) as error:
            await message.answer(str(error))
        except DeliveryError:
            await message.answer('Перевод сохранён. Чеки будут отправлены повторно.')
        return True


    @staticmethod
    async def accounting_cash_chat_command(commands,message,command):
        if not commands.is_cash_chat(message.chat.id):
            return False
        words=(message.text or '').split()
        currency_change=command[1:] in commands.transfer_aliases and len(words)>1
        if currency_change and len(words)==3 and _RE_CASH_REQUEST_ID.fullmatch(words[-1]):
            return False
        from app.thai_russian_commands import CASH, FX_CASH, RECEIVE
        if not currency_change and command not in {*CASH,*FX_CASH,*RECEIVE,
                '/start','/дай','/кошелек','/кошелёк','/выписка','/обнулить','/регистрация','/привязать','/закрыть','/перевод'}:
            return False
        if (getattr(message,'forward_origin',None) or getattr(message,'forward_from',None)) and currency_change:
            return True
        from telegram_adapters.statements import statements_kb
        await commands.authorize(message.from_user.id)
        if command=='/выписка':
            if len(words)>2:
                from domain.finance.models import InvalidPosting
                raise InvalidPosting('/выписка [валюта]')
            code=commands.resolve_instrument(words[1]) if len(words)==2 else 'THB_CASH'
            await WalletsHandler._accounting_statement_page(commands,message,None,code,cash=True)
            return True
        text=await commands.execute(telegram_user_id=message.from_user.id,chat_id=message.chat.id,
            message_id=message.message_id,text=message.text,effective_at=message.date,
            chat_name=get_chat_name(message),actor_name=message.from_user.full_name)
        if currency_change:
            operation=await commands._app.pool.fetchval('SELECT id FROM financial_operations WHERE idempotency_key=$1',f'tg:{message.chat.id}:{message.message_id}')
            await commands.deliver_wallet_adjustment(message.bot,operation)
        else:
            await message.answer(text,parse_mode='HTML' if text.startswith('<code>') else None,reply_markup=statements_kb())
        return True

    @staticmethod
    def register_accounting_wallets(router,commands):
        from domain.finance.models import FinanceError, InvalidPosting
        from services.finance.outbox import DeliveryError

        async def statement(cq):
            if not isinstance(cq.message,Message):
                await cq.answer('Сообщение недоступно',show_alert=True)
                return
            try:
                cash=commands.is_cash_chat(cq.message.chat.id)
                if cash:
                    await commands.authorize(cq.from_user.id)
                client = None if cash else await commands._app.trading_queries.client_by_chat(cq.message.chat.id)
                if client is None and not cash:
                    raise InvalidPosting('Сначала /start в этом клиентском чате')
                if cq.data in ('stmt:month','stmt:all'):
                    await handle_stmt_callback(cq,accounting_service=commands._app.wallet_interactions,
                        client_id=client,timezone='Asia/Bangkok',cash_queries=commands._app.queries if cash else None)
                else:
                    _,_,code,before = (cq.data or '').split(':')
                    if code not in set(commands.transfer_aliases.values()):
                        raise InvalidPosting('Неизвестный счёт')
                    await WalletsHandler._accounting_statement_page(commands,cq.message,client,code,int(before) or None,cash=cash)
                    await cq.answer()
            except (FinanceError,ValueError) as error:
                await cq.answer(str(error)[:190],show_alert=True)

        async def undo(cq):
            if not isinstance(cq.message,Message):
                await cq.answer('Сообщение недоступно',show_alert=True)
                return
            try:
                from datetime import UTC, datetime
                from zoneinfo import ZoneInfo
                actor = await commands.authorize(cq.from_user.id)
                now = datetime.now(UTC)
                undo_service=commands._app.manual_cash.undo_by_message if commands.is_cash_chat(cq.message.chat.id) else commands._app.wallet_interactions.accounting_undo
                result = await undo_service(chat_id=cq.message.chat.id,
                    message_id=cq.message.message_id,actor_id=actor,effective_at=now,
                    business_date=now.astimezone(ZoneInfo('Asia/Bangkok')).date())
                await commands.deliver_wallet_adjustment(cq.bot,result['original_id'])
                await cq.answer('Операция отменена')
            except (FinanceError,ValueError) as error:
                await cq.answer(str(error)[:190],show_alert=True)
            except DeliveryError:
                await cq.answer('Операция отменена. Сообщение будет обновлено повторно.',show_alert=True)

        async def zero(cq):
            if not isinstance(cq.message,Message):
                await cq.answer('Сообщение недоступно',show_alert=True)
                return
            try:
                from datetime import UTC, datetime
                from zoneinfo import ZoneInfo
                if commands.is_cash_chat(cq.message.chat.id):
                    raise InvalidPosting('Общую кассу нельзя обнулить как клиента')
                phase,mid = (cq.data or '')[5:].split(':')
                if phase not in ('yes','no'):
                    raise InvalidPosting('Некорректное подтверждение')
                actor = await commands.authorize(cq.from_user.id)
                now = datetime.now(UTC)
                result = await commands._app.wallet_interactions.resolve_zero(
                    key=f'tg:{cq.message.chat.id}:{int(mid)}:zero',actor_id=actor,confirmed=phase=='yes',
                    effective_at=now,business_date=now.astimezone(ZoneInfo('Asia/Bangkok')).date())
                await cq.message.edit_text('Обнуление отклонено' if result['cancelled'] else
                    'Клиент обнулён и закрыт. Корректирующие проводки и история сохранены.')
                await cq.answer()
            except (FinanceError,ValueError) as error:
                await cq.answer(str(error)[:190],show_alert=True)
        router.callback_query.register(statement,F.data.startswith('stmt:'))
        router.callback_query.register(undo,F.data.startswith('undo:'))
        router.callback_query.register(zero,F.data.startswith('zero:'))

    @staticmethod
    async def _accounting_statement_page(commands,message,client,code,before=None,*,cash=False):
        from telegram_adapters.statements import statements_kb
        if cash:
            found=await commands._app.queries.cash_history(code,before_id=before,limit=16)
            page={'text':WalletTextBuilder.statement_text(code,found[:15]),'next_before_id':found[14]['id'] if len(found)>15 else None}
        else:
            page = await commands._app.wallet_interactions.statement_page(client_id=client,instrument=code,before_id=before)
        buttons = []
        if page['next_before_id']:
            buttons.append([InlineKeyboardButton(text='Далее →',callback_data=f"stmt:page:{code}:{page['next_before_id']}")])
        if before:
            buttons.append([InlineKeyboardButton(text='К последним операциям',callback_data=f'stmt:page:{code}:0')])
        buttons.extend(statements_kb().inline_keyboard)
        await message.answer(page['text'],parse_mode=None,reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    @staticmethod
    async def accounting_wallet_command(commands,message,command):
        from domain.finance.models import InvalidPosting
        from services.finance.outbox import DeliveryError
        from telegram_adapters.statements import statements_kb
        words = (message.text or '').split()
        currency_change = command[1:] in commands.transfer_aliases and len(words)>1
        if currency_change and len(words)==3 and _RE_CASH_REQUEST_ID.fullmatch(words[-1]):
            return False
        if command not in ('/кошелек','/кошелёк','/дай','/выписка','/обнулить') and not currency_change:
            return False
        if (getattr(message,'forward_origin',None) or getattr(message,'forward_from',None)) and (currency_change or command=='/обнулить'):
            return True
        if command=='/обнулить':
            if len(words)!=1:
                raise InvalidPosting('Использование: /обнулить')
            actor = await commands.authorize(message.from_user.id)
            client = await commands._app.trading_queries.client_by_chat(message.chat.id)
            if client is None or message.chat.id==commands.operations_chat_id:
                raise InvalidPosting('/обнулить доступна только в чате клиента')
            preview = await commands._app.wallet_interactions.prepare_zero(
                key=f'tg:{message.chat.id}:{message.message_id}:zero',actor_id=actor,client_id=client)
            rows = [f"⚠️ Обнулить и закрыть клиента «{preview['client_name']}»?",
                    'Будут созданы корректирующие проводки. История сохранится.','Текущие остатки:']
            rows.extend(f"{row['instrument_code']}: {row['balance']}" for row in preview['wallets'])
            await message.answer('\n'.join(rows),parse_mode=None,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text='Подтвердить обнуление',callback_data=f'zero:yes:{message.message_id}'),
                InlineKeyboardButton(text='Отклонить',callback_data=f'zero:no:{message.message_id}')]]))
            return True
        if command=='/выписка':
            client = await commands._app.trading_queries.client_by_chat(message.chat.id)
            if client is None or len(words)>2:
                raise InvalidPosting('/выписка [валюта] — в зарегистрированном клиентском чате')
            code = commands.resolve_instrument(words[1]) if len(words)==2 else 'THB_CASH'
            await WalletsHandler._accounting_statement_page(commands,message,client,code)
            return True
        text = await commands.execute(telegram_user_id=message.from_user.id,chat_id=message.chat.id,
            message_id=message.message_id,text=message.text,effective_at=message.date,
            chat_name=get_chat_name(message),actor_name=message.from_user.full_name)
        if currency_change:
            operation = await commands._app.pool.fetchval('SELECT id FROM financial_operations WHERE idempotency_key=$1',
                f'tg:{message.chat.id}:{message.message_id}')
            try:
                await commands.deliver_wallet_adjustment(message.bot,operation)
            except DeliveryError:
                await message.answer('Корректировка сохранена. Сообщение будет доставлено повторно.')
        else:
            await message.answer(text,parse_mode='HTML' if text.startswith('<code>') else None,
                reply_markup=statements_kb())
        return True
