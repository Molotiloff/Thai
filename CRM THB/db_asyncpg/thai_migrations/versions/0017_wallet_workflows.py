"""Wallet undo, audited closure confirmation and message links."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0017'
down_revision = 'thai_0016'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0017_wallet_workflows.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
