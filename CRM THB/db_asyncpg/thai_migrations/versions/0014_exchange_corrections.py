"""Atomic exchange corrections and persisted message links."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0014'
down_revision = 'thai_0013'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0014_exchange_corrections.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
