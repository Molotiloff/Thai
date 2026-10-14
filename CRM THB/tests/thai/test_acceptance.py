"""Release acceptance: real use cases, recovery and immutable period snapshots."""

from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

import asyncpg
import pytest

from app.container import ApplicationContainer
from domain.finance.models import PeriodClosed
from tests.postgres import disposable_thai_database
from tests.thai.test_capital import accrue, contribute, withdraw
from tests.thai.test_expenses import common, ledger, record, totals
from tests.thai.test_expenses import pay as settle
from tests.thai.test_posting import balance
from tests.thai.test_profit import PERIOD, close, configure

NOV = date(2026, 11, 2)


async def database_contents(pool):
    tables = await pool.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    # Table names come exclusively from PostgreSQL's own catalog.
    return {
        r["tablename"]: await pool.fetchval(
            'SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text FROM "'
            + r["tablename"].replace('"', '""')
            + '" t'
        )
        for r in tables
    }


async def scenario(finance):
    app, pool = finance["app"], finance["pool"]
    assert await pool.fetchval("SELECT count(*) FROM financial_operations") == 0
    created = await app.capital.create_agreement(
        **common(finance, "investor", PERIOD),
        monthly_interest_percent=Decimal(2),
        name="Acceptance investor",
    )
    agreement = await pool.fetchval(
        "SELECT id FROM capital_agreements WHERE created_operation_id=$1", created.id
    )
    await contribute(finance, agreement, key="usdt", day=PERIOD, quantity="1000")
    await contribute(
        finance,
        agreement,
        key="thb-one",
        day=PERIOD,
        instrument="THB_CASH",
        quantity="35000",
        rate="35",
    )
    await contribute(
        finance,
        agreement,
        key="thb-two",
        day=date(2026, 10, 2),
        instrument="THB_CASH",
        quantity="18000",
        rate="36",
    )
    owner = await pool.fetchval(
        "SELECT id FROM capital_agreements WHERE principal_kind='owner_equity' ORDER BY id LIMIT 1"
    )
    await contribute(
        finance,
        owner,
        key="owner-capital",
        day=await pool.fetchval("SELECT opened_on FROM capital_agreements WHERE id=$1", owner),
        quantity="2000",
    )
    lender = (
        await app.clients.create_client(
            key="lender", actor_id=finance["actor"], name="Acceptance lender", group_code="partner"
        )
    )["client_id"]
    await app.wallets.deposit(
        **common(finance, "loan"), client_id=lender, instrument="USDT", quantity="100"
    )
    customer = (
        await app.clients.create_client(
            key="customer", actor_id=finance["actor"], name="Acceptance customer"
        )
    )["client_id"]
    _, dany = await configure(finance)
    await app.best_change.record(
        **common(finance, "best-change"),
        operation="sale",
        quantity="3500",
        market_rate="35",
        client_rate="36",
        mode="wallet",
        client_id=customer,
    )
    await record(finance)
    assert await totals(finance) == {("daniyar", "pe", "THB"): 200, ("skyex", "pe", "THB"): 400}
    assert all(w["balance"] == 0 for w in await app.client_queries.wallets(dany))
    await accrue(finance, agreement, approved=Decimal(10))
    report = await app.profit_queries.report(PERIOD)
    assert (
        report["income_usdt"],
        report["business_expenses_usdt"],
        report["investor_interest_usdt"],
        report["net_profit_usdt"],
    ) == (35, Decimal("22.85714286"), 10, Decimal("2.14285714"))
    await app.expenses.close(
        key="statement",
        actor_id=finance["actor"],
        period=PERIOD,
        effective_at=common(finance, "unused")["effective_at"],
    )
    statement = await app.expense_queries.statement(PERIOD)
    closed = await close(finance)
    assert await close(finance) == closed
    snapshot = await app.profit_queries.closure(PERIOD)
    assert [r["amount_usdt"] for r in snapshot["accruals"]] == [Decimal("1.07142857")] * 2
    assert (
        next(
            w["balance"]
            for w in await app.client_queries.wallets(dany)
            if w["instrument_code"] == "USDT"
        )
        == 35
    )
    for owner in ("pe", "skyex"):
        await app.profit.pay(
            **common(finance, "profit:" + owner, NOV),
            recipient=owner,
            instrument="USDT",
            quantity="1.071428",
        )
    await app.capital.pay_interest(
        **common(finance, "interest-pay", NOV),
        agreement_id=agreement,
        instrument="USDT",
        quantity="10",
        reason="Approved manager payout",
    )
    await settle(finance, key="dany-settle", amount="200", day=NOV)
    await settle(finance, key="sky-settle", amount="400", debtor="skyex", day=NOV)
    assert not await totals(finance)
    assert await ledger(finance, "owner_profit_payable") == Decimal("0.00000114")
    assert (await app.capital_queries.detail(agreement))["interest_due_usdt"] == 0
    before = await balance(finance, "THB_CASH")
    returned = await withdraw(finance, agreement, instrument="THB_CASH", quantity="40000", day=NOV)
    detail = await app.capital_queries.detail(agreement)
    assert detail["principal_usdt"] == Decimal("1361.11111111")
    thb_lots = [lot for lot in detail["lots"] if lot["instrument_code"] == "THB_CASH"]
    assert [lot["remaining_quantity"] for lot in thb_lots] == [0, 13000]
    await app.capital.reverse(
        **common(finance, "undo-return", NOV),
        agreement_id=agreement,
        original_id=returned.id,
        reason="Acceptance storno",
    )
    assert await balance(finance, "THB_CASH") == before
    assert await app.profit_queries.closure(PERIOD) == snapshot
    assert await app.expense_queries.statement(PERIOD) == statement
    assert (await app.profit_queries.report(PERIOD))["net_profit_usdt"] == report["net_profit_usdt"]
    with pytest.raises(PeriodClosed):
        await app.wallets.deposit(
            **common(finance, "late-loan"), client_id=lender, instrument="USDT", quantity="1"
        )
    assert (
        await pool.fetchval("""SELECT count(*) FROM (
        SELECT l.operation_id,sum(CASE side WHEN 'debit' THEN amount_usdt ELSE -amount_usdt END) AS delta
        FROM journal_lines l GROUP BY l.operation_id HAVING sum(CASE side WHEN 'debit' THEN amount_usdt ELSE -amount_usdt END)<>0
    ) s""")
        == 0
    )
    return await app.profit_queries.report(PERIOD), snapshot


async def test_blank_database_month_payout_fifo_rebuild_and_backup_restore(finance, tmp_path):
    report, snapshot = await scenario(finance)
    pool, app = finance["pool"], finance["app"]
    balances = await pool.fetch(
        "SELECT account_id,balance,carrying_usdt FROM posted_account_balances ORDER BY account_id"
    )
    inventory = await pool.fetch(
        "SELECT instrument_code,quantity,cost_usdt FROM inventory_balances ORDER BY instrument_code"
    )
    await pool.execute("UPDATE account_balances SET debit_signed_native=0,debit_signed_usdt=0")
    await pool.execute("UPDATE inventory_balances SET quantity=0,cost_usdt=0")
    await app.projections.rebuild(finance["actor"])
    assert (
        await pool.fetch(
            "SELECT account_id,balance,carrying_usdt FROM posted_account_balances ORDER BY account_id"
        )
        == balances
    )
    assert (
        await pool.fetch(
            "SELECT instrument_code,quantity,cost_usdt FROM inventory_balances ORDER BY instrument_code"
        )
        == inventory
    )
    maintenance = finance["maintenance"]
    backup = tmp_path / "acceptance.dump"
    await maintenance.backup(backup)
    original = await database_contents(pool)
    with disposable_thai_database("crm_thai_restored") as restored_url:
        await maintenance.restore(backup, urlsplit(restored_url).path.strip("/"))
        restored = await asyncpg.create_pool(restored_url, min_size=1, max_size=2)
        try:
            assert await database_contents(restored) == original
            recovered = ApplicationContainer.build(restored)
            assert await recovered.profit_queries.report(PERIOD) == report
            assert await recovered.profit_queries.closure(PERIOD) == snapshot
            with pytest.raises(ValueError, match="empty"):
                await maintenance.restore(backup, urlsplit(restored_url).path.strip("/"))
        finally:
            await restored.close()
    with pytest.raises(ValueError, match="source"):
        await maintenance.restore(backup, await pool.fetchval("SELECT current_database()"))
    with pytest.raises(ValueError, match="already exists"):
        await maintenance.backup(backup)
    backup.write_bytes(backup.read_bytes() + b"corruption")
    with pytest.raises(ValueError, match="checksum"):
        await maintenance.restore(backup, "crm_thai_unused_target")


async def test_runtime_starts_with_only_thai_config_and_no_external_services(
    finance, tmp_path, monkeypatch
):
    from httpx import ASGITransport, AsyncClient

    import app.setup_handlers
    import bot_app
    from config import Config

    config = finance["config"]
    (tmp_path / ".env.thai").write_text(
        "CRM_DATABASE_SCHEMA=thai\nDATABASE_URL="
        + config.database_url
        + "\nCRM_JWT_SECRET="
        + config.jwt_secret
        + "\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://legacy:unused@127.0.0.1:5432/forbidden")
    monkeypatch.setenv("BOT_TOKEN", "legacy-token-must-not-be-used")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/missing/legacy-google.json")
    observed = []

    class Server:
        def __init__(self, config):
            self.app = config.app

        async def serve(self):
            async with AsyncClient(
                transport=ASGITransport(app=self.app), base_url="http://test"
            ) as http:
                result = await http.get("/openapi.json")
                assert result.status_code == 200
                assert "/api/v1/clients/transfers" in result.json()["paths"]
                assert (await http.get("/api/v1/session")).status_code == 401
                observed.append(True)

    def forbidden(*args, **kwargs):
        raise AssertionError("Optional external service should not start")

    monkeypatch.setattr(bot_app.uvicorn, "Server", Server)
    monkeypatch.setattr(bot_app, "Bot", forbidden)
    monkeypatch.setattr(app.setup_handlers, "TronscanGateway", forbidden)
    await bot_app.BotApp(Config.from_env()).run()
    assert observed == [True]
