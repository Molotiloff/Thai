"""Grant /mgr staff full CRM permissions without changing financial data."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0022'
down_revision = 'thai_0021'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0022_manager_full_access.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
