from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import CallbackQuery, Chat, LinkPreviewOptions, Message, User

from handlers.start import SKYEX_INFO_TEXT, StartHandler
from middlewares.silent_accounting_chats import SilentAccountingChatsMiddleware


def _message(text: str, *, chat_id: int = -200) -> Message:
    return Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="group", title="Поэты"),
        from_user=User(id=42, is_bot=False, first_name="Manager"),
        text=text,
    )


async def test_silent_middleware_routes_only_start_and_wallet_changes() -> None:
    downstream = AsyncMock(return_value="downstream")
    start = AsyncMock(return_value="start")
    wallet = AsyncMock(return_value="wallet")
    middleware = SilentAccountingChatsMiddleware(
        {-200},
        start_handler=start,
        wallet_change_handler=wallet,
    )

    assert await middleware(downstream, _message("/start"), {}) == "start"
    assert await middleware(downstream, _message("/RUB 100"), {}) == "wallet"
    assert await middleware(downstream, _message("/баотпр"), {}) == "wallet"
    assert await middleware(downstream, _message("/дай"), {}) is None
    assert await middleware(downstream, _message("обычное сообщение"), {}) is None
    assert await middleware(downstream, _message("/дай", chat_id=-300), {}) == "downstream"

    assert start.await_count == 1
    assert wallet.await_count == 2
    assert downstream.await_count == 1


async def test_silent_middleware_swallows_callbacks() -> None:
    downstream = AsyncMock()
    middleware = SilentAccountingChatsMiddleware(
        {-200},
        start_handler=AsyncMock(),
        wallet_change_handler=AsyncMock(),
    )
    callback = CallbackQuery(
        id="callback-1",
        from_user=User(id=42, is_bot=False, first_name="Manager"),
        chat_instance="instance",
        message=_message("old bot message"),
        data="undo:RUB:+:100",
    )

    assert await middleware(downstream, callback, {}) is None
    downstream.assert_not_awaited()


async def test_start_registers_silent_chat_without_answer() -> None:
    bootstrap = AsyncMock()
    sync_registry = AsyncMock()
    handler = StartHandler(
        bootstrap,
        silent_chat_ids={-200},
        on_silent_wallet_ready=sync_registry,
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(
            id=-200,
            title="Поэты",
            first_name=None,
            last_name=None,
            username=None,
        ),
        answer=AsyncMock(),
    )

    await handler.on_start(message)

    bootstrap.ensure_client_wallet.assert_awaited_once_with(
        chat_id=-200,
        chat_name="Поэты",
    )
    sync_registry.assert_awaited_once_with()
    message.answer.assert_not_awaited()


async def test_info_sends_single_formatted_message() -> None:
    handler = StartHandler(AsyncMock())
    message = SimpleNamespace(answer=AsyncMock())

    await handler._cmd_info(message)

    message.answer.assert_awaited_once_with(
        SKYEX_INFO_TEXT,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    assert "https://t.me/skyex_support" in SKYEX_INFO_TEXT
    assert "https://sky-ex.ru/" in SKYEX_INFO_TEXT
    assert len(SKYEX_INFO_TEXT) < 4096
