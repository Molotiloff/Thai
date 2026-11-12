"""Telegram IDs are unique within a chat, including multiple messages per operation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock


def telegram_mock():
    counters = {}

    async def send(chat_id, text, **kwargs):
        counters[chat_id] = counters.get(chat_id, 899) + 1
        return SimpleNamespace(message_id=counters[chat_id])

    return SimpleNamespace(send_message=AsyncMock(side_effect=send), edit_message_text=AsyncMock())
