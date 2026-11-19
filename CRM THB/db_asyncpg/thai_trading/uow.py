from db_asyncpg.thai_profit.uow import AsyncpgProfitUnitOfWork
from db_asyncpg.thai_trading.history import TradingHistoryRepository
from db_asyncpg.thai_trading.manual_profit import ManualProfitRepository
from db_asyncpg.thai_trading.repository import TradingRepository


class AsyncpgTradingUnitOfWork(AsyncpgProfitUnitOfWork):
    async def __aenter__(self):
        await super().__aenter__()
        self.trades = TradingRepository(self._connection)
        self.history = TradingHistoryRepository(self._connection)
        self.manual_profit = ManualProfitRepository(self._connection)
        return self
