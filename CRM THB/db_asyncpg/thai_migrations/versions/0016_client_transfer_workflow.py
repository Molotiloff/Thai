"""Audited client transfer confirmation and atomic corrections."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0016'
down_revision = 'thai_0015'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0016_client_transfer_workflow.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
