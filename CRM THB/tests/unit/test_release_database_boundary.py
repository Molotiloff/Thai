"""No release utility may accidentally contact the Russian PostgreSQL server."""

from unittest.mock import AsyncMock

import pytest

from config import Config
from db_asyncpg.migrator import AlembicMigrator
from db_asyncpg.schema_identity import require_thai_database_url


@pytest.mark.parametrize('url',[
    'postgresql://user:private@localhost:5432/db',
    'postgresql://user:private@localhost/db',
    'postgresql://user:private@localhost:5433/db?port=5432',
    'postgresql://user:private@localhost:5433/db?host=old',
    'postgresql://user:private@localhost:bad/db',
    'postgresql:///db',
])
async def test_all_entrypoints_reject_unsafe_dsn_before_io(url,tmp_path,monkeypatch):
    path=tmp_path/'.env.thai'
    path.write_text('CRM_DATABASE_SCHEMA=thai\nDATABASE_URL='+url+'\nCRM_JWT_SECRET='+'x'*32+'\n')
    with pytest.raises(ValueError,match='5432') as error:
        Config.load(path)
    assert 'private' not in str(error.value)
    called=AsyncMock(side_effect=AssertionError('Must not connect'))
    monkeypatch.setattr('db_asyncpg.migrator.create_async_engine',called)
    monkeypatch.setattr('db_asyncpg.migrator.command.upgrade',called)
    migrator=AlembicMigrator(url,schema='thai')
    with pytest.raises(ValueError):
        await migrator.status()
    with pytest.raises(ValueError):
        migrator._upgrade_sync()
    called.assert_not_called()


def test_migration_bootstrap_reads_only_explicit_thai_file(tmp_path,monkeypatch,capsys):
    from db_asyncpg import bootstrap
    target='postgresql://new:private@localhost:5433/thai'
    (tmp_path/'.env.thai').write_text('CRM_DATABASE_SCHEMA=thai\nDATABASE_URL='+target+'\n')
    (tmp_path/'.env').write_text('DATABASE_URL=postgresql://old:private@localhost:5432/russian\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DATABASE_URL','postgresql://old:private@localhost:5432/russian')
    monkeypatch.setattr('sys.argv',['bootstrap','--schema','thai','--status'])
    observed=[]
    class Migrator:
        def __init__(self,url,*,schema):
            observed.append((url,schema))
        async def status(self):
            return {'schema':'thai','port':5433}
    monkeypatch.setattr(bootstrap,'AlembicMigrator',Migrator)
    bootstrap.main()
    assert observed==[(target,'thai')]
    assert 'private' not in capsys.readouterr().out


def test_disposable_database_refuses_5432_before_createdb(monkeypatch):
    from tests.postgres import disposable_thai_database
    monkeypatch.setenv('THAI_TEST_DATABASE_URL','postgresql://user:private@localhost:5432/forbidden')
    monkeypatch.setattr('tests.postgres.shutil.which',lambda _:True)
    monkeypatch.setattr('tests.postgres.subprocess.run',lambda *a,**kw:pytest.fail('Must not run createdb'))
    with pytest.raises(ValueError,match='5432'):
        with disposable_thai_database('unsafe'):
            pytest.fail('Must not create database')


def test_thai_environment_expansion_cannot_inherit_old_connection(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL','postgresql://old:private@localhost:5432/russian')
    path=tmp_path/'.env.thai'
    path.write_text('CRM_DATABASE_SCHEMA=thai\nDATABASE_URL=${DATABASE_URL}\nCRM_JWT_SECRET='+'x'*32+'\n')
    with pytest.raises(ValueError):
        Config.load(path)
    assert require_thai_database_url('postgresql://user:private@localhost:5433/thai').port==5433
