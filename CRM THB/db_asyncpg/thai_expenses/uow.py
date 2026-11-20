from db_asyncpg.thai_expenses.journal import ExpenseJournalRepository
from db_asyncpg.thai_expenses.repository import ExpenseRepository
from db_asyncpg.thai_trading.uow import AsyncpgTradingUnitOfWork


class AsyncpgExpenseUnitOfWork(AsyncpgTradingUnitOfWork):
    async def __aenter__(self):
        await super().__aenter__()
        self.journal = ExpenseJournalRepository(self._connection)
        self.expenses = ExpenseRepository(self._connection)
        return self
