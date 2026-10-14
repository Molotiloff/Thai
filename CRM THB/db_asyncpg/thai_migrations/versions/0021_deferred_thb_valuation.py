"""Track client THB without a deposit quote and price pooled funds when used."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0021'
down_revision = 'thai_0020'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0021_deferred_thb_valuation.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
