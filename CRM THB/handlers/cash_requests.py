from __future__ import annotations

from aiogram import F
from aiogram.types import Message

from domain.finance.models import FinanceError, InvalidPosting
from services.cash_requests.constants import CB_DEAL_CANCEL, CB_DEAL_DONE, CB_DEAL_READY
from services.finance.outbox import DeliveryError
from telegram_adapters.message_context import get_chat_name


class CashRequestsHandler:
    @staticmethod
    def register_accounting(router, commands):
        async def cash_status(callback):
            if not isinstance(callback.message, Message):
                await callback.answer('Исходная карточка недоступна', show_alert=True)
                return
            message = callback.message
            try:
                result = await commands.cash_status(telegram_user_id=callback.from_user.id,
                    chat_id=message.chat.id, message_id=message.message_id, data=callback.data or '')
                await commands.deliver_cash_request(callback.bot, result['deal_id'])
            except (FinanceError, ValueError) as error:
                await callback.answer(str(error)[:190], show_alert=True)
                return
            except DeliveryError:
                await callback.answer('Статус сохранён. Карточка будет обновлена повторной отправкой.', show_alert=True)
                return
            await callback.answer('Статус заявки обновлён')

        async def obsolete_confirmation(callback):
            await callback.answer('Старая кнопка отключена. Создайте кассовую заявку новой командой.', show_alert=True)

        router.callback_query.register(cash_status, F.data.startswith((CB_DEAL_READY, CB_DEAL_DONE, CB_DEAL_CANCEL)))
        router.callback_query.register(obsolete_confirmation, F.data=='thai:cash:confirm')

    @staticmethod
    async def accounting_request(commands, message):
        try:
            reply = message.reply_to_message
            if reply and reply.from_user and reply.from_user.id==message.bot.id:
                deal_id = await commands.cash_request_by_message(message.chat.id, reply.message_id)
                if deal_id is None:
                    raise InvalidPosting('Ответьте на карточку кассовой заявки')
                await commands.edit_cash_request(telegram_user_id=message.from_user.id,
                    chat_id=message.chat.id, message_id=message.message_id, request_id=deal_id,
                    text=message.text, actor_name=message.from_user.full_name, effective_at=message.date)
            else:
                await commands.execute(telegram_user_id=message.from_user.id, chat_id=message.chat.id,
                    message_id=message.message_id, text=message.text, effective_at=message.date,
                    chat_name=get_chat_name(message), actor_name=message.from_user.full_name)
                deal_id = await commands.cash_request_by_command(message.chat.id,message.message_id)
            await commands.deliver_cash_request(message.bot, deal_id)
        except (FinanceError, ValueError) as error:
            await message.answer(str(error))
        except DeliveryError:
            await message.answer('Заявка сохранена. Карточка будет отправлена повторно.')
