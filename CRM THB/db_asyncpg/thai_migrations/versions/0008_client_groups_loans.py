"""Thai: stable groups, client directory audit and wallet-based loans."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0008'
down_revision = 'thai_0007'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0008_client_groups_loans.sql')


def downgrade():
    raise NotImplementedError('CRM history migrations are append-only')
