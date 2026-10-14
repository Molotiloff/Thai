from db_asyncpg.thai_clients.uow import AsyncpgClientUnitOfWork
from db_asyncpg.thai_profit.repository import ProfitRepository


class AsyncpgProfitUnitOfWork(AsyncpgClientUnitOfWork):
    async def __aenter__(self):
        await super().__aenter__()
        self.profit = ProfitRepository(self._connection)
        return self
