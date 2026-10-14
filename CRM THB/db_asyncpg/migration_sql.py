"""SQL transport for the Thai Alembic revision tree."""

from __future__ import annotations

import re
from pathlib import Path

from alembic import context, op
from sqlalchemy.util import await_only

_TX_STATEMENT_RE = re.compile(r"^\s*(BEGIN|COMMIT)\s*;\s*$", re.IGNORECASE | re.MULTILINE)


def execute_sql_path(path: Path) -> None:
    sql = _TX_STATEMENT_RE.sub("", path.read_text(encoding="utf-8"))
    if context.is_offline_mode():
        op.execute(sql)
        return
    asyncpg_connection = op.get_bind().connection.driver_connection
    await_only(asyncpg_connection.execute(sql))
