"""Two confirmations, one atomic cash exchange."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0015'
down_revision = 'thai_0014'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0015_cash_exchange_confirmations.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
