"""Explicit historical profit/loss entries without cash or wallet movements."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0027'
down_revision = 'thai_0026'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0027_manual_profit.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
