from db_asyncpg.thai_clients.uow import AsyncpgClientUnitOfWork
from db_asyncpg.thai_trading.repository import TradingRepository


class AsyncpgTradingUnitOfWork(AsyncpgClientUnitOfWork):
    async def __aenter__(self):
        await super().__aenter__()
        self.trades = TradingRepository(self._connection)
        return self
