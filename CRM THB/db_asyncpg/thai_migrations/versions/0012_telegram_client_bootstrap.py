"""Thai: customer chat registration without CRM staff privileges."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0012'
down_revision = 'thai_0011'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0012_telegram_client_bootstrap.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
