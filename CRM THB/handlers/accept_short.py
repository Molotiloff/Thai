from __future__ import annotations

from aiogram import F
from aiogram.types import Message

from domain.finance.models import FinanceError, InvalidPosting
from services.finance.outbox import DeliveryError
from telegram_adapters.message_context import get_chat_name


def _is_forwarded_message(message: Message) -> bool:
    return any(
        getattr(message, attr, None) is not None
        for attr in (
            "forward_origin",
            "forward_from",
            "forward_from_chat",
            "forward_sender_name",
            "forward_signature",
            "forward_date",
            "forward_from_message_id",
        )
    )


class AcceptShortHandler:
    @staticmethod
    def register_accounting(router, commands):
        async def cancel(callback):
            if not isinstance(callback.message, Message):
                await callback.answer('Исходная карточка недоступна',show_alert=True)
                return
            try:
                result = await commands.cancel_exchange(telegram_user_id=callback.from_user.id,
                    chat_id=callback.message.chat.id,message_id=callback.message.message_id,
                    data=callback.data or '')
                await commands.deliver_exchange_request(callback.bot,result['request_id'])
            except (FinanceError,ValueError) as error:
                await callback.answer(str(error)[:190],show_alert=True)
                return
            except DeliveryError:
                await callback.answer('Заявка отменена. Карточка будет обновлена повторно.',show_alert=True)
                return
            await callback.answer('Заявка отменена')
        router.callback_query.register(cancel,F.data.startswith('req_cancel:'))

    @staticmethod
    async def accounting_exchange(commands, message):
        if _is_forwarded_message(message):
            return
        try:
            reply = message.reply_to_message
            if reply:
                if not reply.from_user or reply.from_user.id!=message.bot.id:
                    raise InvalidPosting('Чтобы изменить заявку, ответьте на оригинальную карточку БОТА')
                result = await commands.edit_exchange(telegram_user_id=message.from_user.id,
                    chat_id=message.chat.id,message_id=message.message_id,reply_message_id=reply.message_id,
                    text=message.text,actor_name=_actor_name(message),effective_at=message.date)
                request_id = result['request_id']
            else:
                await commands.execute(telegram_user_id=message.from_user.id,chat_id=message.chat.id,
                    message_id=message.message_id,text=message.text,effective_at=message.date,
                    chat_name=get_chat_name(message),actor_name=_actor_name(message))
                request_id = await commands.exchange_by_command(message.chat.id,message.message_id)
            await commands.deliver_exchange_request(message.bot,request_id)
            if message.chat.id==commands.operations_chat_id:
                return
            summary = await commands.execute(telegram_user_id=message.from_user.id,chat_id=message.chat.id,
                message_id=message.message_id,text='/дай',effective_at=message.date,chat_name=get_chat_name(message))
            await message.reply(summary,**({'parse_mode':'HTML'} if summary.startswith('<code>') else {}))
        except (FinanceError,ValueError) as error:
            await message.answer(str(error))
        except DeliveryError:
            await message.answer('Заявка сохранена. Карточка будет обновлена повторно.')

    @staticmethod
    async def accounting_cancel_reply(commands, message):
        reply = message.reply_to_message
        if not reply or not reply.from_user or reply.from_user.id!=message.bot.id:
            return False
        try:
            result = await commands.cancel_exchange_reply(telegram_user_id=message.from_user.id,
                chat_id=message.chat.id,reply_message_id=reply.message_id)
            if result is None:
                return False
            await commands.deliver_exchange_request(message.bot,result['request_id'])
            await message.answer('Заявка отменена; история сохранена')
        except FinanceError as error:
            await message.answer(str(error))
        except DeliveryError:
            await message.answer('Заявка отменена. Карточка будет обновлена повторно.')
        return True


def _actor_name(message: Message) -> str:
    user = message.from_user
    if not user:
        return "unknown"
    return user.full_name or (f"@{user.username}" if user.username else f"id:{user.id}")
