"""Thai: preserve account classification and financial bindings."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = "thai_0005"
down_revision = "thai_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0005_account_history.sql")


def downgrade() -> None:
    raise NotImplementedError("Financial history migrations are append-only")
