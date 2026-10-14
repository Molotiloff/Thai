from __future__ import annotations

from typing import Any, Protocol


class ExchangeKeyboardPort(Protocol):
    def client_cancel(
        self,
        *,
        request_id: int | str,
        table_request_id: int | str | None,
    ) -> Any: ...

    def request_chat(
        self,
        *,
        request_id: int | str,
        table_request_id: int | str,
    ) -> Any: ...


