"""Thai: posting barriers, historical inventory, period guards and audit metadata."""
from db_asyncpg.thai_migrations.loader import execute_sql_file

revision = "thai_0006"
down_revision = "thai_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_file("0006_financial_posting.sql")


def downgrade() -> None:
    raise NotImplementedError("Financial history migrations are append-only")
