from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

import asyncpg
import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine

from db_asyncpg.migrator import AlembicMigrator
from db_asyncpg.schema_identity import SchemaFamily, SchemaMismatchError, verify_migration_target
from tests.postgres import disposable_thai_database


async def make_operation(db, *, mode="ledger", key="test"):
    user = await db.fetchval("INSERT INTO users(display_name) VALUES ('Manager') RETURNING id")
    return await db.fetchval("""
        INSERT INTO financial_operations(kind,posting_mode,business_date,effective_at,
            created_by,idempotency_key,command_hash)
        VALUES ('test',$1,'2026-10-08',now(),$2,$3,$4) RETURNING id
    """, mode, user, key, "b" * 64)


async def make_client_wallet(db):
    party = await db.fetchval("INSERT INTO parties(name,kind) VALUES ('Client','person') RETURNING id")
    client = await db.fetchval("""
        INSERT INTO clients(party_id,group_code) VALUES ($1,'partner') RETURNING id
    """, party)
    account = await db.fetchval("""
        INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,
            currency_code,instrument_code,party_id)
        VALUES ('test-wallet','liability','client_wallet','credit','THB','THB_CASH',$1) RETURNING id
    """, party)
    await db.execute("""
        INSERT INTO client_wallet_accounts(client_id,instrument_code,ledger_account_id)
        VALUES ($1,'THB_CASH',$2)
    """, client, account)
    return party, client, account


async def finish(db, operation):
    await db.execute("UPDATE financial_operations SET status='posted' WHERE id=$1", operation)
    await db.execute("SET CONSTRAINTS ALL IMMEDIATE")


async def test_seed_is_new_business_only(thai_connection):
    db = thai_connection
    assert await db.fetchval("SELECT version_num FROM alembic_version_thai") == "thai_0021"
    assert await db.fetchrow("""
        SELECT family,accounting_currency,operational_currency,business_timezone FROM crm_schema_identity
    """) == ("thai", "USDT", "THB", "Asia/Bangkok")
    assert set(await db.fetch("SELECT code FROM instruments")) == {
        (code,) for code in ("THB_CASH", "THB_BANK", "USDT", "USD_WH", "USD_BL", "EUR", "EUR500", "RUB")
    }
    assert await db.fetchval("SELECT count(*) FROM cash_accounts") == 8
    assert await db.fetchval("SELECT count(*) FROM instruments WHERE storage_kind='bank'") == 1
    for table in ("clients", "client_chat_links", "financial_operations", "journal_lines",
                  "fx_rate_snapshots", "capital_contributions", "expenses", "deals", "user_roles"):
        assert await db.fetchval(f"SELECT count(*) FROM {table}") == 0
    assert await db.fetchval("SELECT count(*) FROM users WHERE is_active OR telegram_user_id IS NOT NULL") == 0
    assert await db.fetchval("SELECT count(*) FROM capital_interest_terms WHERE monthly_interest_percent<>0") == 0
    assert await db.fetchval("SELECT sum(balance) FROM posted_account_balances") == 0
    assert await db.fetchval("SELECT value FROM crm_settings WHERE key='loss_policy'") == '"report_only"'


async def test_repeat_upgrade_does_not_seed_twice(thai_database_url, thai_connection):
    before = await thai_connection.fetchval("SELECT count(*) FROM ledger_accounts")
    await AlembicMigrator(thai_database_url, schema="thai").upgrade_to_head()
    assert await thai_connection.fetchval("SELECT count(*) FROM ledger_accounts") == before
    assert await thai_connection.fetchval("SELECT count(*) FROM parties") == 5
    status = await AlembicMigrator(thai_database_url, schema="thai").status()
    assert status["schema"] == "thai"
    assert status["revision"] == "thai_0021"
    assert status["database"].startswith("crm_thai_test_")
    assert set(status) == {"schema", "revision", "database", "port"}


async def test_thai_chain_rejects_unrecognised_database_without_partial_ddl():
    with disposable_thai_database("crm_thai_foreign") as url:
        db = await asyncpg.connect(url)
        try:
            await db.execute("CREATE TABLE unrelated_data(id integer PRIMARY KEY); INSERT INTO unrelated_data VALUES (1)")
            with pytest.raises(SchemaMismatchError):
                await AlembicMigrator(url, schema="thai").upgrade_to_head()
            assert await db.fetchval("SELECT count(*) FROM unrelated_data") == 1
            assert await db.fetchval("SELECT to_regclass('public.alembic_version_thai')") is None
            assert await db.fetchval("SELECT to_regclass('public.currencies')") is None
        finally:
            await db.close()


async def test_thai_migrations_reject_russian_schema_without_writing():
    with disposable_thai_database("crm_thai_foreign_schema") as url:
        db = await asyncpg.connect(url)
        try:
            await db.execute("""
                CREATE TABLE client_accounts(id bigint PRIMARY KEY);
                CREATE TABLE transactions(id bigint PRIMARY KEY);
                CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY);
                INSERT INTO client_accounts VALUES (1);
                INSERT INTO alembic_version VALUES ('0039');
            """)
            with pytest.raises(SchemaMismatchError):
                await AlembicMigrator(url, schema="thai").upgrade_to_head()
            assert await db.fetchval("SELECT count(*) FROM client_accounts") == 1
            assert await db.fetchval("SELECT version_num FROM alembic_version") == "0039"
            assert await db.fetchval("SELECT to_regclass('public.alembic_version_thai')") is None
            assert await db.fetchval("SELECT to_regclass('public.crm_schema_identity')") is None
        finally:
            await db.close()


async def test_thai_database_rejects_foreign_migration_marker(thai_database_url, thai_connection):
    # Test the boundary inside a rolled-back transaction, without changing the shared schema.
    engine = create_async_engine(thai_database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)")
            with pytest.raises(SchemaMismatchError, match="legacy migration marker"):
                await connection.run_sync(lambda sync: verify_migration_target(sync, SchemaFamily.THAI))
            await connection.rollback()
    finally:
        await engine.dispose()


async def test_client_wallet_can_be_negative_without_negative_cash(thai_db):
    db = thai_db
    party, client, wallet = await make_client_wallet(db)
    op = await make_operation(db)
    income = await db.fetchval("SELECT id FROM ledger_accounts WHERE purpose='trading_income'")
    await db.execute("""
        INSERT INTO journal_lines VALUES
            ($1,1,$2,'THB','debit',3500,100,'historical',NULL),
            ($1,2,$3,'USDT','credit',100,100,'base',NULL)
    """, op, wallet, income)
    await finish(db, op)
    assert await db.fetchval("SELECT balance FROM partner_loan_balances WHERE client_id=$1", client) == -3500
    assert await db.fetchval("SELECT sum(b.balance) FROM cash_accounts c JOIN posted_account_balances b ON b.account_id=c.ledger_account_id") == 0
    await db.execute("UPDATE clients SET is_active=FALSE WHERE id=$1", client)
    assert await db.fetchval("SELECT count(*) FROM journal_lines") == 2
    assert await db.fetchval("SELECT balance FROM posted_account_balances WHERE account_id=$1", wallet) == -3500
    assert await db.fetchval("SELECT count(*) FROM partner_loan_balances") == 0
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute("UPDATE ledger_accounts SET normal_side='debit' WHERE id=$1", wallet)
    assert party > 0


async def test_physical_cash_cannot_be_negative(thai_db):
    db = thai_db
    _, _, wallet = await make_client_wallet(db)
    cash = await db.fetchval("SELECT ledger_account_id FROM cash_accounts WHERE instrument_code='THB_CASH'")
    with pytest.raises(asyncpg.CheckViolationError, match="Physical cash"):
        async with db.transaction():
            op = await make_operation(db)
            await db.execute("""
                INSERT INTO journal_lines VALUES
                    ($1,1,$2,'THB','debit',3500,100,'historical',NULL),
                    ($1,2,$3,'THB','credit',3500,100,'historical',NULL)
            """, op, wallet, cash)
            await finish(db, op)
    assert await db.fetchval("SELECT count(*) FROM financial_operations") == 0


@pytest.mark.parametrize("table,column", [("inventory_balances", "quantity"),
                                        ("inventory_balances", "cost_usdt")])
async def test_physical_inventory_cannot_be_negative(thai_db, table, column):
    with pytest.raises(asyncpg.CheckViolationError):
        async with thai_db.transaction():
            await thai_db.execute(f"UPDATE {table} SET {column}=-1 WHERE instrument_code='THB_CASH'")


async def test_used_profit_terms_allow_no_retroactive_change(thai_db):
    db = thai_db
    term = await db.fetchval("SELECT id FROM profit_share_terms")
    op = await make_operation(db, mode="memorandum")
    closure = await db.fetchval("""
        INSERT INTO profit_period_closures(period,revision,operation_id,share_term_id,
            income_usdt,business_expenses_usdt,investor_interest_usdt)
        VALUES ('2026-10-01',1,$1,$2,1000,1500,0) RETURNING id
    """, op, term)
    await finish(db, op)
    assert await db.fetchval("SELECT net_profit_usdt FROM profit_period_closures WHERE id=$1", closure) == -500
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute("UPDATE profit_period_closures SET income_usdt=2000 WHERE id=$1", closure)
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute("UPDATE profit_share_terms SET skyex_percent=60,pe_percent=40 WHERE id=$1", term)


async def test_overlapping_terms_are_rejected(thai_db):
    db = thai_db
    user = await db.fetchval("SELECT id FROM users LIMIT 1")
    agreement = await db.fetchval("SELECT id FROM capital_agreements LIMIT 1")
    with pytest.raises(asyncpg.CheckViolationError, match="overlap"):
        async with db.transaction():
            await db.execute("""
                INSERT INTO capital_interest_terms(agreement_id,monthly_interest_percent,valid_from,created_by)
                VALUES ($1,2,'2030-01-01',$2)
            """, agreement, user)
    with pytest.raises(asyncpg.CheckViolationError, match="overlap"):
        async with db.transaction():
            await db.execute("""
                INSERT INTO profit_share_terms(valid_from,skyex_percent,pe_percent,created_by)
                VALUES ('2030-01-01',50,50,$1)
            """, user)


async def test_rate_snapshots_and_schema_identity_are_immutable(thai_db):
    db = thai_db
    user = await db.fetchval("SELECT id FROM users LIMIT 1")
    rate = await db.fetchval("""
        INSERT INTO fx_rate_snapshots(currency_code,quoted_direction,quoted_rate,
            usdt_per_unit,effective_at,created_by)
        VALUES ('USDT','usdt_per_unit',1,1,now(),$1) RETURNING id
    """, user)
    for query, args in (
        ("DELETE FROM fx_rate_snapshots WHERE id=$1", (rate,)),
        ("DELETE FROM crm_schema_identity", ()),
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            async with db.transaction():
                await db.execute(query, *args)


async def test_posted_document_cannot_be_changed_or_extended(thai_db):
    db = thai_db
    op = await make_operation(db, mode="memorandum")
    category = await db.fetchval("INSERT INTO expense_categories(name) VALUES ('Personal') RETURNING id")
    user = await db.fetchval("SELECT id FROM users LIMIT 1")
    pe = await db.fetchval("SELECT party_id FROM business_participants WHERE code='pe'")
    rate = await db.fetchval("""
        INSERT INTO fx_rate_snapshots(currency_code,quoted_direction,quoted_rate,
            usdt_per_unit,effective_at,created_by)
        VALUES ('USDT','usdt_per_unit',1,1,now(),$1) RETURNING id
    """, user)
    expense = await db.fetchval("""
        INSERT INTO expenses(operation_id,category_id,currency_code,amount,amount_usdt,
            rate_snapshot_id,payer_kind,payer_party_id)
        VALUES ($1,$2,'USDT',100,100,$3,'party',$4) RETURNING id
    """, op, category, rate, pe)
    await db.execute("INSERT INTO expense_share_inputs VALUES ($1,'pe',100)",expense)
    await db.execute("INSERT INTO expense_allocations VALUES ($1,$2,100,100,100)",expense,pe)
    await finish(db, op)
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute("UPDATE expenses SET amount=200 WHERE id=$1", expense)
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute("INSERT INTO expense_share_inputs VALUES ($1,'pe',100)", expense)


async def test_all_history_foreign_keys_restrict_deletion(thai_connection):
    assert await thai_connection.fetchval("""
        SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='public' AND c.contype='f' AND c.confdeltype IN ('c','n')
    """) == 0


def test_migration_tree_is_independent():
    migrator = AlembicMigrator("postgresql://unused/unused", schema="thai")
    script = ScriptDirectory.from_config(migrator._build_config())
    revisions = list(script.walk_revisions())
    assert [r.revision for r in revisions] == [f"thai_{n:04d}" for n in range(21, 0, -1)]
    assert all("thai_migrations" in r.path for r in revisions)


def test_readable_schema_matches_frozen_sql_files():
    root = Path(__file__).resolve().parents[2] / "db_asyncpg"
    expected = "-- GENERATED Thai schema snapshot. Source: thai_migrations/sql/*.sql.\nSET search_path TO public;\n" + "\n".join(
        path.read_text() for path in sorted((root / "thai_migrations/sql").glob("*.sql"))
    )
    assert (root / "schema.sql").read_text() == expected


def dump_public_schema(url: str) -> str:
    """Compare PostgreSQL's resolved DDL, excluding Alembic's installation marker."""
    parsed = urlsplit(url)
    result = subprocess.run(
        ["pg_dump", "-h", parsed.hostname, "-p", str(parsed.port),
         "-U", unquote(parsed.username), "-d", parsed.path.lstrip("/"),
         "--schema-only", "--schema=public", "--no-owner", "--no-acl",
         "--exclude-table=public.alembic_version_thai"],
        env={**os.environ, "PGPASSWORD": unquote(parsed.password or "")},
        check=True, capture_output=True, text=True,
    )
    # Recent pg_dump versions generate a random psql restriction token per dump.
    return "\n".join(
        line for line in result.stdout.splitlines()
        if not line.startswith(("\\restrict ", "\\unrestrict "))
    )


async def test_final_sql_snapshot_matches_migrated_postgres_schema():
    if not shutil.which("pg_dump"):
        pytest.skip("pg_dump is required for resolved schema comparison")
    root = Path(__file__).resolve().parents[2] / "db_asyncpg"
    with disposable_thai_database("crm_thai_migrated") as migrated_url:
        with disposable_thai_database("crm_thai_snapshot") as snapshot_url:
            await AlembicMigrator(migrated_url, schema="thai").upgrade_to_head()
            db = await asyncpg.connect(snapshot_url)
            try:
                await db.execute((root / "schema.sql").read_text())
                assert await db.fetchval("SELECT to_regclass('public.alembic_version_thai')") is None
            finally:
                await db.close()
            migrated = await asyncio.to_thread(dump_public_schema, migrated_url)
            snapshot = await asyncio.to_thread(dump_public_schema, snapshot_url)
            assert snapshot == migrated


async def test_release_initial_database_check_accepts_clean_database(thai_connection):
    root = Path(__file__).resolve().parents[2]
    sql = (root / "deploy/thai/verify_initial_database.sql").read_text()
    # psql's ON_ERROR_STOP meta-command belongs to the CLI, not PostgreSQL SQL.
    sql = "\n".join(line for line in sql.splitlines() if not line.startswith("\\"))
    await thai_connection.execute(sql)


async def test_release_initial_database_check_rejects_used_database(thai_database_url):
    with disposable_thai_database("crm_thai_release_used") as url:
        await AlembicMigrator(url, schema="thai").upgrade_to_head()
        db = await asyncpg.connect(url)
        try:
            await db.execute("INSERT INTO users(display_name, telegram_user_id) VALUES ('Manager', 12345)")
            root = Path(__file__).resolve().parents[2]
            sql = (root / "deploy/thai/verify_initial_database.sql").read_text()
            sql = "\n".join(line for line in sql.splitlines() if not line.startswith("\\"))
            with pytest.raises(asyncpg.RaiseError, match="Expected zero balances and no active users"):
                await db.execute(sql)
        finally:
            await db.close()


async def test_direct_alembic_uses_thai_file_despite_russian_environment(tmp_path, monkeypatch):
    with disposable_thai_database("crm_thai_direct_alembic") as url:
        (tmp_path / ".env.thai").write_text("CRM_DATABASE_SCHEMA=thai\nDATABASE_URL=" + url + "\n")
        (tmp_path / ".env").write_text("DATABASE_URL=postgresql://old@localhost:5432/russian\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DATABASE_URL", "postgresql://old@localhost:5432/russian")
        config = AlembicMigrator(url, schema="thai")._build_config()
        del config.attributes["sqlalchemy_url"]  # Direct CLI has no programmatic URL override.
        await asyncio.to_thread(command.upgrade, config, "head")
        db = await asyncpg.connect(url)
        try:
            assert await db.fetchval("SELECT version_num FROM alembic_version_thai") == "thai_0021"
            assert await db.fetchval("SELECT to_regclass('public.alembic_version')") is None
        finally:
            await db.close()
