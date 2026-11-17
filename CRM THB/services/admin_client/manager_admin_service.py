from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import cast

from db_asyncpg.ports.administration import ManagerReadPort, ManagerRepositoryPort
from domain.finance.models import InvalidPosting
from services.clients.ports import ClientUnitOfWork
from services.finance.ports import FinanceUowFactory
from services.metadata_commands import MetadataCommandService


class ManagerAdminService:
    def __init__(self, repo: ManagerReadPort, *, uow_factory: FinanceUowFactory | None = None) -> None:
        self.repo = repo
        self._factory=uow_factory
        self._commands=MetadataCommandService(uow_factory) if uow_factory else None

    async def _change(self, *, user_id, display_name, enabled, actor_id, key):
        if self._commands is None:
            legacy=cast(ManagerRepositoryPort,self.repo)
            return await (legacy.add_manager(user_id=user_id,display_name=display_name) if enabled
                else legacy.remove_manager(user_id=user_id))
        if actor_id is None or key is None:
            raise InvalidPosting('Не указан автор команды управления доступом')
        async def action(work):
            return await cast(ClientUnitOfWork,work).clients.change_manager(user_id,display_name,enabled)
        result=await self._commands.execute(key=key,actor_id=actor_id,kind='manager_change',
            payload={'telegram_user_id':user_id,'display_name':display_name,'enabled':enabled},
            permission='access.manage',action=action)
        return result['changed']

    @staticmethod
    async def _identity(manager, profile_resolver):
        uid = manager['user_id']
        profile = await profile_resolver(uid) if profile_resolver is not None else None
        username = (profile.get('username') if profile is not None else manager.get('username'))
        username = username.strip().removeprefix('@') if isinstance(username, str) else ''
        if username and re.fullmatch(r'[A-Za-z0-9_]{1,32}', username):
            nickname = '@'+username+(' (последний известный)' if profile is None else '')
        else:
            nickname = 'ник не указан' if profile is not None else 'ник недоступен'
        return f"<code>{uid}</code> — {nickname}"

    async def handle_command(self, text: str, *, actor_id=None, key=None,
                             profile_resolver: Callable[[int], Awaitable[dict | None]] | None = None) -> str:
        text = (text or "").strip()

        m_add = re.match(r"^/mgr(?:@\w+)?\s*\+\s+(\d+)(?:\s+(.+))?$", text, flags=re.I | re.U)
        if m_add:
            user_id = int(m_add.group(1))
            display_name = (m_add.group(2) or "").strip()
            ok = await self._change(user_id=user_id,display_name=display_name,enabled=True,actor_id=actor_id,key=key)
            if not ok:
                return '❌ Не удалось добавить менеджера.'
            managers = await self.repo.list_managers()
            manager = next((row for row in managers if row['user_id'] == user_id),
                           {'user_id': user_id, 'display_name': display_name})
            return '✅ Добавлен менеджер: '+await self._identity(manager, profile_resolver)

        m_del = re.match(r"^/mgr(?:@\w+)?\s*-\s+(\d+)\s*$", text, flags=re.I | re.U)
        if m_del:
            user_id = int(m_del.group(1))
            ok = await self._change(user_id=user_id,display_name='',enabled=False,actor_id=actor_id,key=key)
            if not ok:
                return '❌ Менеджер не найден.'
            return '✅ Удалён менеджер: '+await self._identity({'user_id': user_id}, profile_resolver)

        if self._factory is not None:
            if actor_id is None or key is None:
                raise InvalidPosting('Не указан автор команды управления доступом')
            async with self._factory() as work:
                await cast(ClientUnitOfWork,work).clients.guard_directory(actor_id,key,'access.manage')
                managers=await self.repo.list_managers()
        else:
            managers = await self.repo.list_managers()
        if not managers:
            return "Список менеджеров пуст."

        semaphore = asyncio.Semaphore(5)

        async def identity(manager):
            async with semaphore:
                return await self._identity(manager, profile_resolver)

        identities = await asyncio.gather(*(identity(manager) for manager in managers))
        lines = ['Менеджеры:', *('• '+identity for identity in identities)]
        return "\n".join(lines)
