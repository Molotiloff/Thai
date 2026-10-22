"""Record direct BestChange accruals to Daniyar's personal wallet."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0023'
down_revision = 'thai_0022'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0023_bestchange_direct_profit.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
