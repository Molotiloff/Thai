from domain.finance.models import InvalidPosting
from services.clients.ports import LoanReadPort


def debt_direction(balance):
    return 'exchange_owes_client' if balance>0 else 'client_owes_exchange' if balance<0 else 'settled'


class LoanQueryService:
    def __init__(self, queries: LoanReadPort):
        self._queries = queries

    async def balances(self, *, client_id=None, after_client_id=None, limit=50, include_zero=False):
        if not 1 <= limit <= 100:
            raise InvalidPosting('Размер страницы должен быть от 1 до 100')
        rows = await self._queries.loans(client_id=client_id, after_client_id=after_client_id,
            limit=limit, include_zero=include_zero)
        for row in rows:
            row['debt_direction'] = debt_direction(row['balance'])
            row['history_url'] = f"/api/v1/clients/{row['client_id']}/wallets/{row['instrument_code']}/history"
        ids = sorted({row['client_id'] for row in rows})
        return {'items': rows, 'next_after_client_id': ids[-1] if len(ids)==limit else None}

    async def totals(self, client_id=None):
        return await self._queries.loan_totals(client_id)

    async def history(self, client_id, instrument, *, before_id=None, limit=50):
        if not 1<=limit<=100:
            raise InvalidPosting('Размер страницы должен быть от 1 до 100')
        items = await self._queries.loan_wallet_history(client_id, instrument, before_id, limit)
        return {'items': items, 'next_before_id': items[-1]['id'] if len(items)==limit else None}
