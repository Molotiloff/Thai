"""Audited USDT monitoring and BestChange corrections."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0018'
down_revision = 'thai_0017'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0018_payment_best_change_workflows.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
