"""Thai schema identity and a read-only boundary before running migrations."""

from __future__ import annotations

from enum import StrEnum
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import text
from sqlalchemy.engine import Connection


class SchemaFamily(StrEnum):
    THAI = "thai"


class SchemaMismatchError(RuntimeError):
    """The selected migration family does not own the target database."""


def require_thai_database_url(database_url: str):
    """Validate before connecting: a DSN must never select the Russian server."""
    try:
        parsed = urlsplit(database_url)
        valid = (parsed.scheme == 'postgresql' and parsed.hostname and parsed.username
                 and parsed.path.strip('/') and parsed.port is not None and parsed.port != 5432
                 and not {'host','port','dbname','database','service'} & set(parse_qs(parsed.query))
                 and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Thai PostgreSQL requires explicit host, user, database and separate port; 5432 is forbidden')
    return parsed


async def thai_runtime_status(connection):
    row = await connection.fetchrow("""
        SELECT i.family,i.accounting_currency,i.operational_currency,i.business_timezone,
            v.version_num AS revision,inet_server_port() AS port
        FROM public.crm_schema_identity i CROSS JOIN public.alembic_version_thai v
        WHERE i.singleton
    """, timeout=5)
    return dict(row) if row else {}


def is_thai_runtime_ready(status, expected_revision):
    return (status.get('family')=='thai' and status.get('accounting_currency')=='USDT'
            and status.get('operational_currency')=='THB' and status.get('business_timezone')=='Asia/Bangkok'
            and status.get('revision')==expected_revision and status.get('port') not in (None,5432))


def verify_migration_target(connection: Connection, family: SchemaFamily) -> None:
    """Never adopt an existing unrecognised or foreign schema automatically."""
    tables = set(connection.execute(text("""
        SELECT tablename FROM pg_tables WHERE schemaname = 'public'
    """)).scalars())
    if "crm_schema_identity" in tables:
        actual = connection.execute(text(
            "SELECT family FROM public.crm_schema_identity WHERE singleton"
        )).scalar_one_or_none()
        if actual != family.value:
            raise SchemaMismatchError("Selected migration family does not match database identity")
        if "alembic_version" in tables:
            raise SchemaMismatchError("Thai database contains a legacy migration marker")
        return
    if not tables:
        return
    raise SchemaMismatchError(
        "Target database is not empty and has no matching schema identity; "
        "create a separate empty Thai database"
    )
