from __future__ import annotations

import html
import re
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

    async def handle_command(self, text: str, *, actor_id=None, key=None) -> str:
        text = (text or "").strip()

        m_add = re.match(r"^/mgr(?:@\w+)?\s*\+\s+(\d+)(?:\s+(.+))?$", text, flags=re.I | re.U)
        if m_add:
            user_id = int(m_add.group(1))
            display_name = (m_add.group(2) or "").strip()
            ok = await self._change(user_id=user_id,display_name=display_name,enabled=True,actor_id=actor_id,key=key)
            disp = f" — {html.escape(display_name)}" if display_name else ""
            return (
                f"✅ Добавлен менеджер: <code>{user_id}</code>{disp}"
                if ok
                else "❌ Не удалось добавить менеджера."
            )

        m_del = re.match(r"^/mgr(?:@\w+)?\s*-\s+(\d+)\s*$", text, flags=re.I | re.U)
        if m_del:
            user_id = int(m_del.group(1))
            ok = await self._change(user_id=user_id,display_name='',enabled=False,actor_id=actor_id,key=key)
            return (
                f"✅ Удалён менеджер: <code>{user_id}</code>"
                if ok
                else "❌ Менеджер не найден."
            )

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

        lines = ["Менеджеры:"]
        for manager in managers:
            uid = manager["user_id"]
            name = (manager.get("display_name") or "").strip()
            lines.append(f"• <code>{uid}</code>{(' — ' + html.escape(name)) if name else ''}")
        return "\n".join(lines)
