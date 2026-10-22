"""Remove manual rates and allow native balances before their first exchange."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0024'
down_revision = 'thai_0023'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0024_internal_exchange_rates.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
