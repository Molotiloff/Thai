from __future__ import annotations

from decimal import localcontext

import asyncpg

from db_asyncpg.thai_finance.repositories import (
    AccountsRepository,
    DocumentNotesRepository,
    JournalRepository,
    OutboxRepository,
)
from domain.finance.models import InvalidPosting


class AsyncpgFinanceUnitOfWork:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._connection = None
        self._transaction = None
        self._finished = False

    async def __aenter__(self):
        if self._connection is not None:
            raise RuntimeError("Unit of work is already active")
        self._connection = await self._pool.acquire()
        try:
            self._transaction = self._connection.transaction()
            await self._transaction.start()
            self._finished = False
            self.accounts = AccountsRepository(self._connection)
            self.journal = JournalRepository(self._connection)
            self.outbox = OutboxRepository(self._connection)
            self.documents = DocumentNotesRepository(self._connection)
            self._decimal_context = localcontext()
            self._decimal_context.__enter__().prec = 80
            return self
        except BaseException:
            await self._pool.release(self._connection)
            self._connection = None
            raise

    async def commit(self):
        if self._transaction is None or self._finished:
            raise RuntimeError("Unit of work is not active")
        # Validate deferred guards while rollback is still available on the transaction.
        await self._connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
        self._finished = True
        await self._transaction.commit()

    async def __aexit__(self, exc_type, exc, traceback):
        try:
            if not self._finished:
                await self._transaction.rollback()
        finally:
            try:
                await self._pool.release(self._connection)
            finally:
                self._connection = None
                self._transaction = None
                self._decimal_context.__exit__(exc_type, exc, traceback)
        if isinstance(exc, asyncpg.CheckViolationError | asyncpg.ForeignKeyViolationError |
                      asyncpg.UniqueViolationError):
            raise InvalidPosting("Данные нарушают ограничения финансового журнала") from exc
