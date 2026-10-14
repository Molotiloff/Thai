"""Execute the E1 prototype only in a newly created, disposable database."""

from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from tests.postgres import disposable_thai_database


@pytest.fixture(scope="session")
def design_database_url():
    with disposable_thai_database("crm_thai_design") as url:
        yield url


@pytest_asyncio.fixture(scope="session")
async def prototype_connection(design_database_url):
    connection = await asyncpg.connect(design_database_url)
    try:
        schema = Path(__file__).resolve().parents[2] / "architecture/thai/schema_draft.sql"
        await connection.execute(schema.read_text())
        yield connection
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def design_db(prototype_connection):
    transaction = prototype_connection.transaction()
    await transaction.start()
    try:
        yield prototype_connection
    finally:
        await transaction.rollback()


@pytest_asyncio.fixture
async def ledger(design_db):
    db = design_db
    await db.execute("""
        INSERT INTO currencies(code,name,precision) VALUES
            ('THB','Baht',2),('USDT','Tether',6);
        INSERT INTO instruments(code,currency_code,name,storage_kind,precision) VALUES
            ('THB_CASH','THB','Cash baht','cash',2),
            ('THB_BANK','THB','Bank baht','bank',2),
            ('USDT','USDT','Tether','crypto',6);
        INSERT INTO client_groups(code,name,exposes_in_loans) VALUES
            ('balance','Balance',false),('partner','Partner',true);
    """)
    user = await db.fetchval("INSERT INTO users(display_name) VALUES ('Manager') RETURNING id")
    party = await db.fetchval(
        "INSERT INTO parties(name,kind) VALUES ('Client','person') RETURNING id"
    )
    client = await db.fetchval(
        "INSERT INTO clients(party_id,group_code) VALUES ($1,'partner') RETURNING id", party
    )
    cash = await db.fetchval("""
        INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code,instrument_code)
        VALUES ('cash','asset','cash','debit','THB','THB_CASH') RETURNING id
    """)
    wallet = await db.fetchval("""
        INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code,instrument_code,party_id)
        VALUES ('wallet','liability','client_wallet','credit','THB','THB_CASH',$1) RETURNING id
    """, party)
    await db.execute("INSERT INTO cash_accounts VALUES ('THB_CASH',$1)", cash)
    await db.execute("""
        INSERT INTO client_wallet_accounts(client_id,instrument_code,ledger_account_id)
        VALUES ($1,'THB_CASH',$2)
    """, client, wallet)
    return {"db": db, "user": user, "party": party, "client": client,
            "cash": cash, "wallet": wallet}
