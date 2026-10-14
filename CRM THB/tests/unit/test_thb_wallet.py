from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.admin_client.client_bootstrap_service import ClientBootstrapService


async def test_start_adds_thb_to_existing_client_wallet() -> None:
    repo = SimpleNamespace(
        ensure_client=AsyncMock(return_value=42),
        snapshot_wallet=AsyncMock(
            return_value=[
                {"currency_code": code}
                for code in ("USD", "USDW", "USDT", "RUB", "EUR", "EUR500", "РУБПЕР")
            ]
        ),
        add_currency=AsyncMock(),
    )

    client_id = await ClientBootstrapService(repo).ensure_client_wallet(
        chat_id=-100, chat_name="Клиент"
    )

    assert client_id == 42
    repo.add_currency.assert_awaited_once_with(42, "THB", 2)
