"""Expense reimbursements and Daniyar shares in existing USDT client wallets."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0026'
down_revision = 'thai_0025'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0026_expense_wallets.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
