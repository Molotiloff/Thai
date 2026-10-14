from __future__ import annotations

import hashlib
from typing import cast

from db_asyncpg.ports.workflows import ClientWalletRepositoryPort
from domain.finance.models import IdempotencyConflict, InvalidPosting, canonical_json
from services.clients.ports import ClientUnitOfWork
from services.finance.ports import FinanceUowFactory

DEFAULT_CURRENCIES: list[tuple[str, int]] = [
    ("USD", 2),
    ("USDW", 2),
    ("USDT", 2),
    ("RUB", 2),
    ("EUR", 2),
    ("THB", 2),
    ("EUR500", 2),
    ("РУБПЕР", 2),
]


class ClientBootstrapService:
    def __init__(
        self, repo: ClientWalletRepositoryPort | None = None, *,
        uow_factory: FinanceUowFactory | None = None,
    ) -> None:
        if (repo is None) == (uow_factory is None):
            raise ValueError("Configure exactly one wallet repository or accounting unit of work")
        self.repo = repo
        self._factory = uow_factory

    async def ensure_client_wallet(self, *, chat_id: int, chat_name: str) -> int:
        if self.repo is None:
            raise RuntimeError("Use ensure_telegram_client_wallet for transactional accounting")
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
        rows = await self.repo.snapshot_wallet(client_id)
        existing_codes = {str(row["currency_code"]).upper() for row in rows}

        for code, precision in DEFAULT_CURRENCIES:
            if code.upper() not in existing_codes:
                await self.repo.add_currency(client_id, code, precision)

        return client_id

    async def ensure_telegram_client_wallet(self, *, key, telegram_user_id, chat_id, chat_name):
        if self._factory is None:
            raise RuntimeError("Transactional registration requires an accounting unit of work")
        name = chat_name.strip()
        if not name or len(name) > 300 or not chat_id or telegram_user_id <= 0:
            raise InvalidPosting("Некорректные данные клиентского чата")
        if not key or len(key) > 200:
            raise InvalidPosting("Некорректный ключ регистрации")
        fingerprint = hashlib.sha256(canonical_json({
            "telegram_user_id": telegram_user_id, "chat_id": chat_id, "name": name,
        }).encode()).hexdigest()
        async with self._factory() as work:
            uow = cast(ClientUnitOfWork, work)
            await uow.clients.lock_chat(chat_id)
            previous = await uow.clients.find_bootstrap(key)
            if previous and previous["fingerprint"] != fingerprint:
                raise IdempotencyConflict("Команда регистрации уже использована с другими данными")
            client_id = await uow.clients.ensure_chat_client(chat_id, name)
            if previous is None:
                await uow.clients.save_bootstrap(key, fingerprint, telegram_user_id, chat_id, client_id, name)
            await uow.commit()
            return client_id
