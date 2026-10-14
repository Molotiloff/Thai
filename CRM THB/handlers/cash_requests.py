from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from db_asyncpg.ports.administration import ManagerRepositoryPort
from services.cash_requests import (
    CashRequestService,
    CreateCashRequestParams,
    EditCashRequestParams,
    RequestDealCancelService,
    RequestDealDoneService,
    RequestDealReadyService,
    RequestIssueParams,
    RequestIssueService,
    RequestTimeParams,
    RequestTimeService,
)
from services.cash_requests.constants import (
    CB_DEAL_CANCEL,
    CB_DEAL_DONE,
    CB_DEAL_READY,
    CB_ISSUE_DONE,
)
from services.cash_requests.workflow_models import CashRequestStatusCommand
from telegram_adapters import (
    AiogramCallbackReplier,
    AiogramMessageReplier,
    AiogramMessenger,
)
from telegram_adapters.auth import (
    require_manager_or_admin_callback,
    require_manager_or_admin_message,
)
from telegram_adapters.message_context import actor_from_message, get_chat_name


class CashRequestsHandler:
    def __init__(
        self,
        *,
        repo: ManagerRepositoryPort,
        admin_chat_ids: set[int],
        admin_user_ids: set[int],
        request_service: CashRequestService,
        request_time_service: RequestTimeService,
        request_issue_service: RequestIssueService,
        request_deal_ready_service: RequestDealReadyService,
        request_deal_done_service: RequestDealDoneService,
        request_deal_cancel_service: RequestDealCancelService,
    ) -> None:
        self.router = Router()
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids)
        self.admin_user_ids = set(admin_user_ids)
        self.request_service = request_service
        self.request_time_service = request_time_service
        self.request_issue_service = request_issue_service
        self.request_deal_ready_service = request_deal_ready_service
        self.request_deal_done_service = request_deal_done_service
        self.request_deal_cancel_service = request_deal_cancel_service
        self._register()

    async def _request(self, message: Message) -> None:
        if not await self._authorize_message(message):
            return
        bot = message.bot
        if not isinstance(bot, Bot):
            await message.answer("Бот недоступен.")
            return
        parsed = self.request_service.parse_command(message.text or "")
        if parsed is None:
            await message.answer(self.request_service.help_text())
            return
        messenger = AiogramMessenger(bot)
        replier = AiogramMessageReplier(message)
        client_group = (
            "internal_wallet" if message.chat.id in self.admin_chat_ids else None
        )

        async def sync_schedule(city: str) -> None:
            await self.request_service.schedule_service.sync_board(
                messenger=messenger,
                city=city,
            )

        reply = message.reply_to_message
        is_bot_reply = bool(
            reply
            and reply.from_user
            and reply.from_user.id == bot.id
            and (reply.text or reply.caption)
        )
        if is_bot_reply and reply is not None:
            await self.request_service.edit_cash_request.execute_core(
                EditCashRequestParams(
                    chat_id=message.chat.id,
                    chat_name=get_chat_name(message),
                    parsed=parsed,
                    old_text=_plain_text(reply),
                    reply_msg_id=reply.message_id,
                    editor_name=actor_from_message(message),
                    client_group=client_group,
                ),
                messenger=messenger,
                replier=replier,
                sync_schedule_board=sync_schedule,
                sync_old_city_schedule=sync_schedule,
            )
            return
        await self.request_service.create_cash_request.execute_core(
            CreateCashRequestParams(
                chat_id=message.chat.id,
                chat_name=get_chat_name(message),
                parsed=parsed,
                creator_name=actor_from_message(message),
                source_message_id=message.message_id,
                client_group=client_group,
            ),
            messenger=messenger,
            replier=replier,
            sync_schedule_board=sync_schedule,
        )

    async def _time(self, message: Message) -> None:
        if not await self._authorize_message(message):
            return
        if not self.request_time_service.router_service.is_request_chat(message.chat.id):
            return
        bot = message.bot
        if not isinstance(bot, Bot):
            await message.answer("Бот недоступен.")
            return
        hhmm = self.request_time_service.parse_time((message.text or "").strip())
        if hhmm is None:
            await message.answer("Формат: /время 10:00")
            return
        reply = message.reply_to_message
        if reply is None:
            await message.answer("Нужно ответить командой /время на сообщение с заявкой.")
            return
        target_html, is_caption = _html_text(reply)
        if not target_html.strip():
            await message.answer("Нужно ответить на сообщение с текстом.")
            return
        messenger = AiogramMessenger(bot)

        async def sync_schedule(city: str) -> None:
            await self.request_time_service.schedule_service.sync_board(
                messenger=messenger,
                city=city,
            )

        await self.request_time_service.execute_core(
            RequestTimeParams(
                request_chat_id=message.chat.id,
                target_chat_id=reply.chat.id,
                target_message_id=reply.message_id,
                target_text_html=target_html,
                target_text_plain=_plain_text(reply),
                is_caption=is_caption,
                reply_markup=reply.reply_markup,
                hhmm=hhmm,
            ),
            messenger=messenger,
            replier=AiogramMessageReplier(message),
            sync_schedule_board=sync_schedule,
        )

    async def _issue(self, callback: CallbackQuery) -> None:
        if not await self._authorize_callback(callback):
            return
        message = callback.message
        if not isinstance(message, Message):
            await callback.answer()
            return
        await self.request_issue_service.execute_core(
            RequestIssueParams(
                chat_id=message.chat.id,
                chat_name=get_chat_name(message),
                message_id=message.message_id,
                card_text=message.text or "",
                callback_data=callback.data or "",
            ),
            messenger=AiogramMessenger(_callback_bot(callback)),
            replier=AiogramCallbackReplier(callback),
        )

    async def _done(self, callback: CallbackQuery) -> None:
        await self._status(callback, self.request_deal_done_service)

    async def _ready(self, callback: CallbackQuery) -> None:
        await self._status(callback, self.request_deal_ready_service)

    async def _cancel(self, callback: CallbackQuery) -> None:
        await self._status(callback, self.request_deal_cancel_service)

    async def _status(self, callback: CallbackQuery, service) -> None:
        if not await self._authorize_callback(callback):
            return
        message = callback.message
        if not isinstance(message, Message):
            await callback.answer()
            return
        if not service.router_service.is_request_chat(message.chat.id):
            await callback.answer(
                "Кнопка доступна только в чате сделок.",
                show_alert=True,
            )
            return
        card_text, is_caption = _html_text(message)
        messenger = AiogramMessenger(_callback_bot(callback))

        async def sync_schedule(city: str) -> None:
            await service.schedule_service.sync_board(messenger=messenger, city=city)

        await service.execute_core(
            CashRequestStatusCommand(
                chat_id=message.chat.id,
                message_id=message.message_id,
                card_text=card_text,
                is_caption=is_caption,
                callback_data=callback.data or "",
                actor_tg_user_id=(
                    callback.from_user.id if callback.from_user is not None else None
                ),
            ),
            messenger=messenger,
            replier=AiogramCallbackReplier(callback),
            sync_schedule_board=sync_schedule,
        )

    async def _authorize_message(self, message: Message) -> bool:
        return await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        )

    async def _authorize_callback(self, callback: CallbackQuery) -> bool:
        return await require_manager_or_admin_callback(
            self.repo,
            callback,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        )

    def _register(self) -> None:
        self.router.message.register(
            self._request,
            Command(*self.request_service.supported_commands),
        )
        self.router.message.register(self._time, Command("время"))
        self.router.callback_query.register(
            self._issue,
            F.data.startswith(CB_ISSUE_DONE),
        )
        self.router.callback_query.register(
            self._ready,
            F.data.startswith(CB_DEAL_READY),
        )
        self.router.callback_query.register(
            self._done,
            F.data.startswith(CB_DEAL_DONE),
        )
        self.router.callback_query.register(
            self._cancel,
            F.data.startswith(CB_DEAL_CANCEL),
        )

    @staticmethod
    def register_accounting(router, commands):
        async def cash_status(callback):
            from domain.finance.models import FinanceError
            from services.finance.outbox import DeliveryError
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
        from domain.finance.models import FinanceError, InvalidPosting
        from services.finance.outbox import DeliveryError
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


def _plain_text(message: Message) -> str:
    return message.caption or "" if message.caption is not None and not message.text else message.text or ""


def _html_text(message: Message) -> tuple[str, bool]:
    if message.caption is not None and not message.text:
        return (message.html_text or message.caption or ""), True
    return (message.html_text or message.text or ""), False


def _callback_bot(callback: CallbackQuery) -> Bot:
    bot = callback.bot
    if not isinstance(bot, Bot):
        raise RuntimeError("Callback bot is unavailable")
    return bot
