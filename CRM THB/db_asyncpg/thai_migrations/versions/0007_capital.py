"""Thai: capital directory, investor decisions and active FIFO projections."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = "thai_0007"
down_revision = "thai_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0007_capital.sql")


def downgrade() -> None:
    raise NotImplementedError("Financial history migrations are append-only")
