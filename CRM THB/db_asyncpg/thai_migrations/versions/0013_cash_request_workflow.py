"""Cash request lifecycle over the existing accounting ledger."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0013'
down_revision = 'thai_0012'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0013_cash_request_workflow.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
