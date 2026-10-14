"""Применение единственной Thai-цепочки Alembic при старте приложения."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from db_asyncpg.schema_identity import (
    SchemaFamily,
    require_thai_database_url,
    verify_migration_target,
)

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AlembicMigrator:
    """Доводит схему БД до head перед запуском бота/API."""

    def __init__(self, database_url: str, project_root: Path = _PROJECT_ROOT,
                 *, schema: SchemaFamily | str) -> None:
        self._database_url = database_url
        self._project_root = project_root
        self._schema = SchemaFamily(schema)

    async def upgrade_to_head(self) -> None:
        # env.py поднимает собственный event loop (asyncio.run) — уводим в поток.
        await asyncio.to_thread(self._upgrade_sync)
        log.info("Alembic: схема %s на актуальной ревизии (head)", self._schema.value)

    async def status(self) -> dict:
        """Read identity and installed revision without migration or credential output."""
        require_thai_database_url(self._database_url)
        url = self._database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.run_sync(lambda sync: verify_migration_target(sync, self._schema))
                installed = await connection.scalar(text("SELECT version_num FROM public.alembic_version_thai"))
                metadata = (await connection.execute(text(
                    "SELECT current_database() AS database, inet_server_port() AS port"
                ))).mappings().one()
                return {"schema": self._schema.value, "revision": installed, **metadata}
        finally:
            await engine.dispose()

    def _upgrade_sync(self) -> None:
        require_thai_database_url(self._database_url)
        command.upgrade(self._build_config(), "head")

    def head_revision(self):
        return ScriptDirectory.from_config(self._build_config()).get_current_head()

    def _build_config(self) -> AlembicConfig:
        config = AlembicConfig(str(self._project_root / "alembic.ini"))
        config.set_main_option(
            "script_location", str(self._project_root / "db_asyncpg" / "thai_migrations")
        )
        # URL через attributes, а не set_main_option: конфиг-интерполяция
        # ломается на '%' в пароле; логирование уже настроено приложением.
        config.attributes["sqlalchemy_url"] = self._database_url
        config.attributes["configure_logger"] = False
        config.attributes["schema_family"] = self._schema.value
        return config
