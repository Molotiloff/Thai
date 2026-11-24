from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from telegram_adapters.city_cash_transfer import (
    send_cash_settlement_balance_to_client,
    send_cash_settlement_evidence_to_client,
)


def _photo_message(*file_ids: str):
    return SimpleNamespace(
        photo=[SimpleNamespace(file_id=file_id) for file_id in file_ids]
    )


async def test_send_cash_settlement_photo_uses_largest_telegram_size() -> None:
    bot = SimpleNamespace(
        send_photo=AsyncMock(),
        send_media_group=AsyncMock(),
    )

    chat_id = await send_cash_settlement_evidence_to_client(
        repo=MagicMock(),
        bot=bot,
        evidence_messages=[_photo_message("small", "large")],
        target_chat_id=-100500,
        target_client_id=42,
    )

    assert chat_id == -100500
    bot.send_photo.assert_awaited_once_with(chat_id=-100500, photo="large")
    bot.send_media_group.assert_not_awaited()


async def test_send_cash_settlement_album_preserves_all_photos() -> None:
    bot = SimpleNamespace(
        send_photo=AsyncMock(),
        send_media_group=AsyncMock(),
    )

    await send_cash_settlement_evidence_to_client(
        repo=MagicMock(),
        bot=bot,
        evidence_messages=[
            _photo_message("first-small", "first"),
            _photo_message("second-small", "second"),
        ],
        target_chat_id=-100500,
        target_client_id=42,
    )

    bot.send_photo.assert_not_awaited()
    bot.send_media_group.assert_awaited_once()
    call = bot.send_media_group.await_args
    assert call.kwargs["chat_id"] == -100500
    assert [item.media for item in call.kwargs["media"]] == ["first", "second"]


async def test_send_cash_settlement_client_balance_for_withdrawal() -> None:
    bot = SimpleNamespace(send_message=AsyncMock())

    chat_id = await send_cash_settlement_balance_to_client(
        repo=MagicMock(),
        bot=bot,
        target_chat_id=-100500,
        target_client_id=42,
        currency_code="RUB",
        signed_amount=Decimal("-10000"),
        balance=Decimal("90000"),
        precision=2,
    )

    assert chat_id == -100500
    bot.send_message.assert_awaited_once_with(
        chat_id=-100500,
        text="Запомнил. -10’000.00 rub\nБаланс: 90’000.00 rub",
    )
