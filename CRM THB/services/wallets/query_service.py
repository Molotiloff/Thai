from __future__ import annotations

from db_asyncpg.ports.workflows import ClientWalletRepositoryPort
from domain.finance.models import InvalidPosting
from services.clients.ports import WalletAuditReadPort
from services.wallets.text_builder import WalletTextBuilder


class WalletQueryService:
    def __init__(
        self, *, repo: ClientWalletRepositoryPort | None = None, text_builder: WalletTextBuilder | None = None,
        accounting_queries: WalletAuditReadPort | None = None
    ) -> None:
        self.repo = repo
        self._accounting = accounting_queries
        self.text_builder = text_builder or WalletTextBuilder()

    async def build_wallet_text(self, *, chat_id: int, chat_name: str) -> str:
        client_id = await self.repo.ensure_client(chat_id, chat_name)
        rows = await self.repo.snapshot_wallet(client_id)
        return self.text_builder.wallet_text(chat_name=chat_name, rows=rows)


    async def statement_page(self, *, client_id, instrument, before_id=None):
        if before_id is not None and before_id<=0:
            raise InvalidPosting('Некорректная страница выписки')
        rows = await self._accounting.wallet_history(client_id,instrument,before_id,16)
        visible = rows[:15]
        return {'rows':visible,'next_before_id':visible[-1]['id'] if len(rows)>15 else None,
                'instrument':instrument,'text':self.text_builder.statement_text(instrument,visible)}

    async def export_transactions(self, *, client_id, since=None, until=None):
        if any(value is not None and (value.tzinfo is None or value.utcoffset() is None) for value in (since,until)):
            raise InvalidPosting('Время выписки должно содержать часовой пояс')
        if since is not None and until is not None and since>=until:
            raise InvalidPosting('Некорректный период выписки')
        return await self._accounting.export_transactions(client_id=client_id,since=since,until=until)
