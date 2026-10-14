from db_asyncpg.thai_clients.uow import AsyncpgClientUnitOfWork
from db_asyncpg.thai_expenses.journal import ExpenseJournalRepository
from db_asyncpg.thai_expenses.repository import ExpenseRepository


class AsyncpgExpenseUnitOfWork(AsyncpgClientUnitOfWork):
    async def __aenter__(self):
        await super().__aenter__()
        self.journal = ExpenseJournalRepository(self._connection)
        self.expenses = ExpenseRepository(self._connection)
        return self
