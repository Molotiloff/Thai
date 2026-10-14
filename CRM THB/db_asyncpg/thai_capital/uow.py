from db_asyncpg.thai_capital.repository import CapitalRepository
from db_asyncpg.thai_finance.uow import AsyncpgFinanceUnitOfWork


class AsyncpgCapitalUnitOfWork(AsyncpgFinanceUnitOfWork):
    async def __aenter__(self):
        await super().__aenter__()
        self.capital = CapitalRepository(self._connection)
        return self
