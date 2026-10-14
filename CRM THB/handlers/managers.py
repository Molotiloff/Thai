# handlers/managers.py
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.admin_client import ManagerAdminService


class ManagersHandler:
    """
    /mgr — управление менеджерами.
    Доступ: только в одном админском чате (admin_chat_id). Любой участник этого чата.
    """
    def __init__(self, service: ManagerAdminService, admin_chat_id: int | None, *, authorize=None) -> None:
        self.service = service
        self.admin_chat_id = int(admin_chat_id) if admin_chat_id is not None else None
        self.authorize=authorize
        self.router = Router()
        self._register()

    def _allowed(self, m: Message) -> bool:
        return bool(m.chat and m.chat.id == self.admin_chat_id)

    async def _cmd_mgr(self, message: Message) -> None:
        if not self._allowed(message):
            await message.answer("⛔ Команда доступна только в админском чате.")
            return

        actor=await self.authorize(message.from_user.id) if self.authorize and message.from_user else None
        kwargs={'actor_id':actor,'key':f'tg:{message.chat.id}:{message.message_id}:manager'} if self.authorize else {}
        text=await self.service.handle_command(message.text or '',**kwargs)
        from services.admin_client.client_directory_service import ClientDirectoryService
        for chunk in ClientDirectoryService._chunk(text):
            await message.answer(chunk,parse_mode='HTML')

    def _register(self) -> None:
        self.router.message.register(self._cmd_mgr, Command("mgr"))
