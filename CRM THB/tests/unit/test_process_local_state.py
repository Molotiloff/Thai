from __future__ import annotations

import gc

from telegram_adapters import ChatLockRegistry


def test_chat_lock_registry_reuses_active_locks_and_releases_idle_entries() -> None:
    registry = ChatLockRegistry()
    first = registry.for_chat(-100)

    assert registry.for_chat(-100) is first
    assert registry.active_count() == 1

    del first
    gc.collect()

    assert registry.active_count() == 0
