"""Restore cash chat commands over pooled Thai cash accounts."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0025'
down_revision = 'thai_0024'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0025_pooled_cash_chat.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
