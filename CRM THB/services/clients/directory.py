import re
from urllib.parse import urlsplit

from domain.finance.models import InvalidPosting
from services.finance.ports import FinanceUowFactory
from services.metadata_commands import MetadataCommandService


class ClientDirectoryService:
    def __init__(self, uow_factory: FinanceUowFactory):
        self._commands = MetadataCommandService(uow_factory)

    async def _execute(self, *, key, actor_id, kind, payload, action):
        return await self._commands.execute(
            key=key,
            actor_id=actor_id,
            kind=kind,
            payload=payload,
            action=lambda uow: action(uow.clients),
        )

    async def create_group(self, *, key, actor_id, code, name):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", code) or not name.strip():
            raise InvalidPosting("Нужны стабильный код группы и непустое имя")
        return await self._execute(
            key=key,
            actor_id=actor_id,
            kind="group_create",
            payload={"code": code, "name": name.strip()},
            action=lambda repo: repo.create_group(code, name.strip()),
        )

    async def rename_group(self, *, key, actor_id, code, name):
        if not name.strip():
            raise InvalidPosting("Имя группы не может быть пустым")
        return await self._execute(
            key=key,
            actor_id=actor_id,
            kind="group_rename",
            payload={"code": code, "name": name.strip()},
            action=lambda repo: repo.rename_group(code, name.strip()),
        )

    async def create_client(
        self, *, key, actor_id, name=None, party_id=None, group_code="balance", chat_id=None
    ):
        if (name is None) == (party_id is None) or (name is not None and not name.strip()):
            raise InvalidPosting("Укажите имя нового клиента или существующего участника")

        async def action(repo):
            result = await repo.create_client(name, party_id, group_code)
            if chat_id is not None:
                await repo.bind_chat(result["client_id"], chat_id)
            return result

        payload = {"name": name, "party_id": party_id, "group_code": group_code}
        if chat_id is not None:
            payload["chat_id"] = chat_id
        return await self._execute(
            key=key, actor_id=actor_id, kind="client_create", payload=payload, action=action
        )

    async def assign_group(self, *, key, actor_id, client_id, group_code, reason):
        if not reason.strip():
            raise InvalidPosting("Причина смены группы обязательна")
        return await self._execute(
            key=key,
            actor_id=actor_id,
            kind="client_group",
            payload={"client_id": client_id, "group_code": group_code, "reason": reason},
            action=lambda repo: repo.assign_group(client_id, group_code),
        )

    async def bind_chat(self, *, key, actor_id, client_id, chat_id):
        return await self._execute(
            key=key,
            actor_id=actor_id,
            kind="chat_bind",
            payload={"client_id": client_id, "chat_id": chat_id},
            action=lambda repo: repo.bind_chat(client_id, chat_id),
        )

    async def close(self, *, key, actor_id, client_id, reason):
        if not reason.strip():
            raise InvalidPosting('Причина закрытия обязательна')
        async def action(work):
            await work.journal.lock_posting_period()
            return await work.clients.close_client(client_id)
        return await self._commands.execute(key=key,actor_id=actor_id,kind='client_close',
            payload={'client_id':client_id,'reason':reason},action=action)

    async def set_invite_link(self, *, key, actor_id, client_id, invite_link):
        if invite_link is not None:
            invite_link = invite_link.strip()
            parsed = urlsplit(invite_link)
            if (parsed.scheme != 'https' or parsed.netloc != 't.me' or not parsed.path.strip('/')
                    or parsed.query or parsed.fragment or len(invite_link)>500):
                raise InvalidPosting('Укажите ссылку https://t.me/...')
        return await self._execute(key=key, actor_id=actor_id, kind='chat_bind',
            payload={'client_id': client_id, 'invite_link': invite_link},
            action=lambda repo: repo.set_invite_link(client_id, invite_link))
