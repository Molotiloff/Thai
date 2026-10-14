"""Thai: BestChange, owner profit, payouts and scoped financial revisions."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0011'
down_revision = 'thai_0010'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0011_profit.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
