"""Find the transitive carrying-cost tail while the posting lock is held."""

import json

from db_asyncpg.thai_clients.queries import ClientQueries
from db_asyncpg.thai_expenses.queries import ExpenseQueries
from db_asyncpg.thai_finance.queries import FinanceQueries
from db_asyncpg.thai_profit.queries import ProfitQueries
from domain.finance.models import ReversalBlocked


class TradingHistoryRepository:
    def __init__(self, connection):
        self._db = connection
        self.expense_queries = ExpenseQueries(connection)
        self.wallet_queries = ClientQueries(connection)
        self.cash_queries = FinanceQueries(connection)
        self.profit_queries = ProfitQueries(connection, connection=connection)

    async def exchange_revalued(self, deal_id, operation_id):
        await self._db.execute("""
            INSERT INTO telegram_outbox(event_key,event_type,payload)
            VALUES ($1,'exchange.revalued',jsonb_build_object('deal_id',$2::bigint))
        """, f'exchange:revalued:{operation_id}', deal_id)

    async def tail(self, operation_id, extra_accounts):
        rows = await self._db.fetch("""
            SELECT o.*, coalesce((o.command_details->>'historical_order')::bigint,o.id) AS ordering,
                ARRAY(SELECT DISTINCT l.account_id FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id
                    WHERE l.operation_id=o.id AND (a.purpose IN ('client_wallet','cash')
                        OR l.unvalued_native<>0 OR l.valuation_component)) AS accounts
            FROM active_financial_originals o
            ORDER BY coalesce((o.command_details->>'historical_order')::bigint,o.id),o.id
        """)
        original = next((row for row in rows if row['id'] == operation_id), None)
        if original is None:
            raise ReversalBlocked('Исходная операция уже отменена; обновите заявку')
        accounts = set(original['accounts']) | set(extra_accounts)
        # Pricing a previously unknown instrument can value several wallets at
        # once. Include those instruments conservatively, even when the original
        # exchange used a different pair and therefore had no such prefix lines.
        ledger_accounts = await self._db.fetch("""
            SELECT id,instrument_code FROM ledger_accounts
            WHERE purpose IN ('cash','client_wallet')
        """)
        instruments = {row['id']: row['instrument_code'] for row in ledger_accounts}

        def expand(ids):
            codes = {instruments.get(identity) for identity in ids} - {None, 'USDT'}
            return set(ids) | {row['id'] for row in ledger_accounts if row['instrument_code'] in codes}

        accounts = expand(accounts)
        affected = {operation_id}
        dependencies = await self._db.fetch('SELECT * FROM financial_operation_dependencies')
        parents = {}
        for dependency in dependencies:
            parents.setdefault(dependency['dependent_operation_id'], set()).add(dependency['source_operation_id'])
        result = []
        for row in rows:
            if row['ordering'] <= original['ordering']:
                continue
            if accounts.intersection(row['accounts']) or affected.intersection(parents.get(row['id'], ())):
                item = dict(row)
                item['details'] = json.loads(row['command_details'])
                item['dependencies'] = sorted(parents.get(row['id'], ()))
                item['deal'] = None
                item['expense_id'] = await self._db.fetchval('SELECT id FROM expenses WHERE operation_id=$1', row['id']) if row['kind'] == 'expense' else None
                if row['kind'] in ('fx_trade', 'best_change'):
                    deal = await self._db.fetchrow('SELECT * FROM deals WHERE operation_id=$1', row['id'])
                    if deal:
                        item['deal'] = {**dict(deal), 'attributes': json.loads(deal['attributes'])}
                        if row['kind'] == 'best_change':
                            item['deal']['daniyar_account_id'] = await self._db.fetchval(
                                'SELECT daniyar_wallet_account_id FROM best_change_calculations WHERE deal_id=$1', deal['id'])
                result.append(item)
                accounts.update(expand(row['accounts']))
                affected.add(row['id'])
        return dict(original), result

    async def best_change_revalued(self, operation_id):
        deal_id = await self._db.fetchval('SELECT id FROM deals WHERE operation_id=$1', operation_id)
        await self._db.execute("""
            INSERT INTO telegram_outbox(event_key,event_type,payload)
            VALUES ($1,'best_change.revalued',jsonb_build_object('deal_id',$2::bigint))
        """, f'best-change:revalued:{operation_id}', deal_id)

    async def expense_revalued(self, original_operation_id):
        await self._db.execute("""
            INSERT INTO telegram_outbox(event_key,event_type,payload)
            VALUES ($1,'expense.revalued',jsonb_build_object('operation_id',$2::bigint))
        """, f'expense:revalued:{original_operation_id}', original_operation_id)
