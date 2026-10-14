"""Read models sharing one PostgreSQL snapshot, independent of runtime providers."""
from decimal import Decimal, localcontext

from db_asyncpg.thai_finance.queries import operation_dict
from db_asyncpg.thai_profit.repository import ProfitRepository
from domain.accounting_policy import summarize
from domain.finance.models import InvalidPosting


class ReportQueries:
    def __init__(self, pool):
        self._pool = pool

    async def session(self, actor):
        async with self._pool.acquire() as db, db.transaction(isolation='repeatable_read', readonly=True):
            user = await db.fetchrow('SELECT id,display_name FROM users WHERE id=$1 AND is_active', actor)
            if user is None:
                raise InvalidPosting('Пользователь не найден')
            roles = [r['role'] for r in await db.fetch('SELECT role FROM user_roles WHERE user_id=$1 ORDER BY role', actor)]
            permissions = [r['permission'] for r in await db.fetch('SELECT DISTINCT p.permission FROM role_permissions p JOIN user_roles r ON r.role=p.role WHERE r.user_id=$1 ORDER BY p.permission', actor)]
            return {**dict(user), 'roles': roles, 'permissions': permissions}

    async def dashboard(self, period):
        async with self._pool.acquire() as db, db.transaction(isolation='repeatable_read', readonly=True):
            with localcontext() as ctx:
                ctx.prec = 80
                stamp = await db.fetchrow('SELECT transaction_timestamp() AS as_of,pg_current_snapshot()::text AS snapshot')
                cash = [dict(r) for r in await db.fetch('''
                    SELECT c.instrument_code,c.ledger_account_id,b.currency_code,b.balance,b.carrying_usdt,b.unvalued_native AS unvalued_quantity
                    FROM cash_accounts c JOIN posted_account_balances b ON b.account_id=c.ledger_account_id ORDER BY c.instrument_code
                ''')]
                wallets = [dict(r) for r in await db.fetch('''
                    SELECT w.instrument_code,b.currency_code,sum(greatest(b.balance,0)) AS exchange_owes,
                        sum(greatest(-b.balance,0)) AS clients_owe,sum(b.balance) AS balance,
                        sum(b.carrying_usdt) AS carrying_usdt,
                        coalesce(sum(b.balance) FILTER(WHERE c.group_code='partner' AND c.is_active),0) AS partner_loans
                    FROM client_wallet_accounts w JOIN clients c ON c.id=w.client_id
                        JOIN posted_account_balances b ON b.account_id=w.ledger_account_id
                    WHERE w.kind='client' GROUP BY w.instrument_code,b.currency_code ORDER BY w.instrument_code
                ''')]
                capital = [dict(r) for r in await db.fetch('''
                    SELECT a.id,a.investor_party_id,p.name,a.principal_kind,
                        coalesce((SELECT sum(b.remaining_principal) FROM capital_lot_balances b WHERE b.agreement_id=a.id),0) AS principal_usdt,
                        coalesce((SELECT sum(i.remaining_usdt) FROM investor_interest_balances i WHERE i.agreement_id=a.id),0) AS interest_due_usdt
                    FROM capital_agreements a JOIN parties p ON p.id=a.investor_party_id ORDER BY a.id
                ''')]
                debts = [dict(r) for r in await db.fetch('''
                    SELECT debtor_party_id,d.name AS debtor,creditor_party_id,c.name AS creditor,currency_code,
                        sum(remaining) AS amount FROM expense_obligation_balances b JOIN parties d ON d.id=b.debtor_party_id
                        JOIN parties c ON c.id=b.creditor_party_id WHERE remaining>0
                    GROUP BY debtor_party_id,d.name,creditor_party_id,c.name,currency_code ORDER BY debtor_party_id,creditor_party_id,currency_code
                ''')]
                repo = ProfitRepository(db)
                sources = await repo.sources(period)
                term = await repo.term(period)
                profit = summarize(sources)
                return {**dict(stamp), 'period': period, 'cash': cash, 'wallets': wallets,
                    'capital': capital, 'capital_usdt': sum((r['principal_usdt'] for r in capital),Decimal(0)),
                    'interest_due_usdt': sum((r['interest_due_usdt'] for r in capital),Decimal(0)),
                    'expense_debts': debts, 'profit': {**profit,'term':term,'sources':sources}}

    async def instruments(self):
        return [dict(r) for r in await self._pool.fetch('SELECT code,currency_code,precision FROM instruments WHERE is_active ORDER BY code')]

    async def client(self, identity):
        row = await self._pool.fetchrow('SELECT c.*,p.name FROM clients c JOIN parties p ON p.id=c.party_id WHERE c.id=$1', identity)
        if row is None:
            raise InvalidPosting('Клиент не найден')
        return dict(row)

    async def operations(self, *, period=None, currency=None, party_id=None, account_id=None, before_id=None, limit=50):
        rows = await self._pool.fetch('''
            SELECT o.* FROM financial_operations o WHERE o.status='posted' AND ($1::date IS NULL OR
                (o.business_date>=$1 AND o.business_date<($1::date+interval '1 month')))
                AND ($5::bigint IS NULL OR o.id<$5)
                AND (($2::text IS NULL AND $3::bigint IS NULL AND $4::bigint IS NULL) OR EXISTS(
                    SELECT 1 FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id WHERE l.operation_id=o.id
                        AND ($2::text IS NULL OR l.currency_code=$2) AND ($3::bigint IS NULL OR a.party_id=$3)
                        AND ($4::bigint IS NULL OR a.id=$4)))
            ORDER BY o.id DESC LIMIT $6
        ''', period, currency, party_id, account_id, before_id, limit)
        return [operation_dict(r) for r in rows]
