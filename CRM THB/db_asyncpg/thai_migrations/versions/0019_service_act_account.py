"""Independent non-financial USDT service account for the requests chat."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0019'
down_revision = 'thai_0018'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0019_service_act_account.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
