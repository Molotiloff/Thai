"""New schema fixtures, deliberately separate from legacy tests/db fixtures."""

import asyncpg
import pytest
import pytest_asyncio

from app.container import ApplicationContainer
from app.thai_maintenance import ThaiDatabaseMaintenance
from config import Config
from db_asyncpg.migrator import AlembicMigrator
from tests.postgres import disposable_thai_database


@pytest.fixture(scope="session")
def thai_database_url():
    with disposable_thai_database("crm_thai_test") as url:
        yield url


@pytest_asyncio.fixture(scope="session")
async def thai_connection(thai_database_url):
    await AlembicMigrator(thai_database_url, schema="thai").upgrade_to_head()
    connection = await asyncpg.connect(thai_database_url)
    try:
        yield connection
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def thai_db(thai_connection):
    transaction = thai_connection.transaction()
    await transaction.start()
    try:
        yield thai_connection
    finally:
        await transaction.rollback()


@pytest_asyncio.fixture
async def finance():
    with disposable_thai_database("crm_thai_finance") as url:
        await AlembicMigrator(url, schema="thai").upgrade_to_head()
        pool = await asyncpg.create_pool(url, min_size=1, max_size=8)
        try:
            actor = await pool.fetchval("""
                INSERT INTO users(display_name,telegram_user_id) VALUES ('Manager',12345) RETURNING id
            """)
            await pool.execute("INSERT INTO user_roles VALUES ($1,'manager'),($1,'owner')", actor)
            rows = await pool.fetch("SELECT instrument_code,ledger_account_id FROM cash_accounts")
            cash = {row["instrument_code"]: row["ledger_account_id"] for row in rows}
            equity = await pool.fetchval("SELECT id FROM ledger_accounts WHERE purpose='retained_result'")
            yield {"pool": pool, "config": Config(url, "x" * 32), "maintenance": ThaiDatabaseMaintenance(url), "app": ApplicationContainer.build(pool),
                   "actor": actor, "cash": cash, "equity": equity}
        finally:
            await pool.close()
