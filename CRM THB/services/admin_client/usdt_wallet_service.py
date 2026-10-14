from __future__ import annotations

import html

from db_asyncpg.ports.administration import SettingsRepositoryPort
from domain.finance.models import InvalidPosting
from domain.tron_wallet import is_probable_tron_wallet
from services.metadata_commands import MetadataCommandService

SETTING_KEY = "USDT_WALLET"


class UsdtWalletService:
    def __init__(self, repo: SettingsRepositoryPort | None = None, *, uow_factory=None, queries=None) -> None:
        self.repo = repo
        self._commands = MetadataCommandService(uow_factory) if uow_factory else None
        self._queries = queries

    async def build_show_message(self) -> str:
        addr = await self.repo.get_setting(SETTING_KEY)
        if not addr:
            return "USDT-кошелёк пока не задан."
        return (
            f"<code>{html.escape(addr)}</code>"
        )

    async def set_from_text(self, text: str) -> str:
        parts = (text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return "Использование: /setwallet <адрес USDT> (или /setкош <адрес>)"

        addr = parts[1].strip()
        if len(addr) < 26 or len(addr) > 128:
            return "Похоже, адрес некорректный. Проверьте и попробуйте снова."

        await self.repo.set_setting(SETTING_KEY, addr)
        return "✅ USDT-кошелёк обновлён."


    async def current_accounting(self, fallback=None):
        return await self._queries.payment_wallet(fallback)

    async def set_accounting(self, *, key,actor_id,address):
        if not is_probable_tron_wallet(address):
            raise InvalidPosting('Нужен корректный адрес USDT TRC-20')
        async def action(work):
            await work.trades.set_payment_wallet(address)
            return {'address':address}
        return await self._commands.execute(key=key,actor_id=actor_id,kind='payment_wallet_set',
            payload={'address':address},action=action,permission='terms.manage')
