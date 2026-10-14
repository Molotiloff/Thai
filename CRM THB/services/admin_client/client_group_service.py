from __future__ import annotations

import html
import re

from db_asyncpg.ports.clients import ClientRepositoryPort


class ClientGroupService:
    def __init__(self, repo: ClientRepositoryPort | None = None, *, directory=None, queries=None, trading_queries=None) -> None:
        self.repo = repo
        self._directory,self._queries,self._trading = directory,queries,trading_queries

    async def assign_from_text(self, text: str) -> str:
        text = (text or "").strip()
        match = re.match(r"^/группа(?:@\w+)?\s+(-?\d+)\s+(.+)$", text, flags=re.IGNORECASE | re.UNICODE)
        if not match:
            return (
                "Использование: /группа <chat_id> <группа>\n"
                "Пример: /группа 123456789 VIP"
            )

        try:
            target_chat_id = int(match.group(1))
        except ValueError:
            return "Некорректный chat_id."

        client_group = match.group(2).strip()
        if not client_group:
            return "Укажите группу после chat_id."

        record = await self.repo.set_client_group_by_chat_id(target_chat_id, client_group)
        if not record:
            return f"Клиент с chat_id={target_chat_id} не найден."

        safe_name = html.escape(record.get("name") or "")
        safe_group = html.escape(record.get("client_group") or "")
        return f"✅ Группа для «{safe_name}» (chat_id={target_chat_id}) установлена: {safe_group}"


    async def assign_accounting(self, *, text, key, actor_id, chat_id):
        from domain.finance.models import InvalidPosting
        parts = (text or '').split(maxsplit=2)
        if len(parts)==3:
            try:
                target_chat = int(parts[1])
            except ValueError:
                raise InvalidPosting('/группа <chat_id> <группа> или /группа <группа> в чате клиента') from None
            name = parts[2]
        elif len(parts)==2:
            target_chat,name = chat_id,parts[1]
        else:
            raise InvalidPosting('/группа <chat_id> <группа> или /группа <группа> в чате клиента')
        client = await self._trading.client_by_chat(target_chat)
        if client is None:
            raise InvalidPosting('Клиентский чат не найден или закрыт')
        def normalize(value):
            return value.casefold().replace('ё','е')
        matches = [g for g in await self._queries.groups()
                   if normalize(name) in (normalize(g['code']),normalize(g['name']))]
        if len(matches)!=1:
            raise InvalidPosting('Группа не найдена или имя неоднозначно; укажите код группы')
        await self._directory.assign_group(key=key,actor_id=actor_id,client_id=client,
            group_code=matches[0]['code'],reason='Смена группы клиента в Telegram')
        return f"✅ Группа чата {target_chat} установлена: {matches[0]['name']}. Остатки сохранены."
