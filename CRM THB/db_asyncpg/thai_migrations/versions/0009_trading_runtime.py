"""Thai: trades, request lifecycle, market quotes and confirmed settlements."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0009'
down_revision = 'thai_0008'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0009_trading_runtime.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
