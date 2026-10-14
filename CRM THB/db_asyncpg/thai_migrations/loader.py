from pathlib import Path

from db_asyncpg.migration_sql import execute_sql_path


def execute_sql_file(filename: str) -> None:
    execute_sql_path(Path(__file__).resolve().parent / "sql" / filename)
