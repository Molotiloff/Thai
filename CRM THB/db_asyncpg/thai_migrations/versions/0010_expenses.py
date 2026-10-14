"""Thai: expenses, debt resolutions and immutable monthly statements."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0010'
down_revision = 'thai_0009'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0010_expenses.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
