"""Alembic environment for the isolated Thai revision tree."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from db_asyncpg.schema_identity import (
    SchemaFamily,
    require_thai_database_url,
    verify_migration_target,
)


def run_environment(family: SchemaFamily) -> None:
    config = context.config
    selected = config.attributes.get("schema_family", family.value)
    if SchemaFamily(selected) != family:
        raise RuntimeError("Alembic script location and schema family disagree")
    if config.config_file_name and config.attributes.get("configure_logger", True):
        fileConfig(config.config_file_name)
    url = config.attributes.get("sqlalchemy_url")
    if not url:
        # Direct Alembic invocation follows the same isolated configuration as bootstrap.
        settings = dotenv_values(".env.thai", interpolate=False)
        if settings.get("CRM_DATABASE_SCHEMA") != "thai":
            raise RuntimeError("Migrations require CRM_DATABASE_SCHEMA=thai in .env.thai")
        url = (settings.get("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required for migrations")
    require_thai_database_url(url)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    version_table = "alembic_version_thai"

    if context.is_offline_mode():
        # Offline SQL cannot verify the destination. The generated script has a SQL guard.
        context.configure(url=url, literal_binds=True, target_metadata=None,
                          version_table=version_table, version_table_schema="public")
        with context.begin_transaction():
            context.run_migrations()
        return

    def migrate(connection):
        with connection.begin():
            connection.execute(text("SET LOCAL search_path TO public"))
            connection.execute(text("SELECT pg_advisory_xact_lock(740810, 1)"))
            verify_migration_target(connection, family)
            context.configure(connection=connection, target_metadata=None,
                              version_table=version_table, version_table_schema="public")
            with context.begin_transaction():
                context.run_migrations()

    async def migrate_async():
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.run_sync(migrate)
        finally:
            await engine.dispose()

    asyncio.run(migrate_async())
