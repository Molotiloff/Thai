from unittest.mock import AsyncMock

import pytest

from bot_app import run_app
from config import Config
from db_asyncpg.migrator import AlembicMigrator


def test_migrator_requires_explicit_family():
    with pytest.raises(TypeError):
        AlembicMigrator("postgresql://unused/unused")


@pytest.mark.parametrize("family", ["legacy", "other", "thia"])
def test_migrator_rejects_unsupported_schema_before_io(family):
    with pytest.raises(ValueError):
        AlembicMigrator("postgresql://unused/unused", schema=family)


def test_url_with_percent_password_uses_attributes_without_interpolation():
    url = "postgresql://user:p%25ss@127.0.0.1:5433/thai"
    config = AlembicMigrator(url, schema="thai")._build_config()
    assert config.attributes["sqlalchemy_url"] == url
    assert config.attributes["schema_family"] == "thai"
    assert config.get_main_option("script_location").endswith("thai_migrations")


@pytest.mark.parametrize('family',['legacy','other'])
def test_branch_config_rejects_other_schema(tmp_path,family):
    path=tmp_path/'.env.thai'
    path.write_text('CRM_DATABASE_SCHEMA='+family+'\nDATABASE_URL=postgresql://unused@localhost:5433/thai\nCRM_JWT_SECRET='+'x'*32+'\n')
    with pytest.raises(RuntimeError):
        Config.from_env(path)


def test_existing_entrypoint_uses_bot_app_and_selected_env(monkeypatch):
    from bot_app import BotApp
    config=Config('postgresql://unused@localhost:5433/thai','x'*32)
    observed=[]
    monkeypatch.setattr(Config,'from_env',lambda path: observed.append(path) or config)
    run=AsyncMock()
    monkeypatch.setattr(BotApp,'run',run)
    run_app(api_only=True)
    assert observed==['.env.thai']
    run.assert_awaited_once_with()


async def test_bot_app_refuses_russian_database_before_any_io(monkeypatch):
    from bot_app import BotApp
    unsafe=Config('postgresql://unused:secret@localhost:5432/russian','x'*32)
    called=AsyncMock(side_effect=AssertionError('Must not connect'))
    monkeypatch.setattr('bot_app.create_pool',called)
    monkeypatch.setattr(AlembicMigrator,'upgrade_to_head',called)
    with pytest.raises(ValueError):
        await BotApp(unsafe,api_only=True).run()
    called.assert_not_called()


def test_only_thai_schema_artifacts_are_available():
    from pathlib import Path

    from db_asyncpg.schema_identity import SchemaFamily

    root = Path(__file__).resolve().parents[2]
    assert list(SchemaFamily) == [SchemaFamily.THAI]
    assert not (root / "alembic-legacy.ini").exists()
    assert not (root / "db_asyncpg/schema_legacy.sql").exists()
    assert not (root / "db_asyncpg/migrations").exists()


def test_bootstrap_rejects_legacy_cli_before_constructing_migrator(monkeypatch):
    from db_asyncpg import bootstrap

    monkeypatch.setattr("sys.argv", ["bootstrap", "--schema", "legacy"])
    monkeypatch.setattr(bootstrap, "AlembicMigrator", lambda *a, **kw: pytest.fail("Must not connect"))
    with pytest.raises(SystemExit) as error:
        bootstrap.main()
    assert error.value.code == 2
