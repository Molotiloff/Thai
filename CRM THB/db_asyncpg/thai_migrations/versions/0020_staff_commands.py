"""Audit manager role changes through existing metadata commands."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0020'
down_revision = 'thai_0019'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_file('0020_staff_commands.sql')


def downgrade():
    raise NotImplementedError('Financial migrations are append-only')
