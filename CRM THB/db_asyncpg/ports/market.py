from __future__ import annotations

from typing import Any, Protocol


class LiveMessageRepositoryPort(Protocol):
    async def upsert_live_message(
        self,
        *,
        chat_id: int,
        message_key: str,
        message_id: int,
    ) -> None: ...

    async def get_live_message(
        self,
        *,
        chat_id: int,
        message_key: str,
    ) -> dict[str, Any] | None: ...

    async def delete_live_message(self, *, chat_id: int, message_key: str) -> bool: ...

    async def list_live_messages(
        self,
        *,
        message_key: str | None = None,
    ) -> list[dict[str, Any]]: ...
