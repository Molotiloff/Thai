"""Thai: seed. Append-only migration; no legacy imports or data transfer."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = 'thai_0004'
down_revision = 'thai_0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file('0004_seed.sql')


def downgrade() -> None:
    raise NotImplementedError('Financial history migrations are append-only; restore a backup for rollback')
