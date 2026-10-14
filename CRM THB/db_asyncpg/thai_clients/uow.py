from db_asyncpg.repositories.act_counter import ActCounterRepo
from db_asyncpg.thai_clients.repository import ClientsRepository
from db_asyncpg.thai_finance.uow import AsyncpgFinanceUnitOfWork


class AsyncpgClientUnitOfWork(AsyncpgFinanceUnitOfWork):
    async def __aenter__(self):
        await super().__aenter__()
        self.clients = ClientsRepository(self._connection)
        self.act = ActCounterRepo(self._pool, connection=self._connection)
        return self
