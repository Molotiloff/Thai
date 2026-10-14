import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from domain.finance.models import InvalidPosting, PeriodClosed, ReversalBlocked
from tests.thai.test_expenses import common, ledger
from tests.thai.test_posting import DAY, INSTANT, balance, fund

PERIOD = date(2026, 10, 1)
NOV = date(2026, 11, 2)


async def configure(finance):
    clients = finance["app"].clients
    buffer = (
        await clients.create_client(key="buffer", actor_id=finance["actor"], name="BestChange")
    )["client_id"]
    dany = (
        await clients.create_client(key="dany", actor_id=finance["actor"], name="Баланс Данияр")
    )["client_id"]
    await finance["app"].best_change.configure(
        **common(finance, "configure"), buffer_client_id=buffer, daniyar_client_id=dany
    )
    return buffer, dany


async def deal(finance, key="bc", **extra):
    return await finance["app"].best_change.record(
        **common(finance, key),
        operation="sale",
        quantity="100",
        market_rate="35",
        client_rate=extra.pop("client_rate", "36"),
        **extra,
    )


async def close(finance, key="close", **extra):
    return await finance["app"].profit.close(
        key=key,
        actor_id=finance["actor"],
        period=PERIOD,
        effective_at=datetime(2026, 11, 1, tzinfo=UTC),
        **extra,
    )


async def test_best_change_cash_and_close_no_duplicate_profit(finance):
    _, dany = await configure(finance)
    await fund(finance, instrument="USDT", quantity=Decimal(100))
    posted = await deal(finance)
    assert await deal(finance) == posted
    assert await balance(finance, "USDT") == (0, 0)
    assert await balance(finance, "THB_CASH") == (3600, Decimal("102.85714286"))
    assert await ledger(finance, "best_change_buffer") == 1
    assert (await finance["app"].profit_queries.report(PERIOD))["net_profit_usdt"] == 1
    closed = await close(finance)
    assert await close(finance) == closed
    assert await ledger(finance, "best_change_buffer") == 0
    wallets = await finance["app"].client_queries.wallets(dany)
    assert next(w["balance"] for w in wallets if w["instrument_code"] == "USDT") == 1
    assert await ledger(finance, "owner_profit_payable") == 1
    assert (await finance["app"].profit_queries.report(PERIOD))["net_profit_usdt"] == 1
    snapshot = await finance["app"].profit_queries.closure(PERIOD)
    assert [a["amount_usdt"] for a in snapshot["accruals"]] == [Decimal(".5"), Decimal(".5")]
    assert len(snapshot["best_change_transfer"]) == 1
    assert snapshot['sources'] and all(row['purpose'] and row['business_date'] for row in snapshot['sources'])
    with pytest.raises(PeriodClosed):
        await deal(finance, key="closed-month")


async def test_wallet_best_change_and_negative_balance_without_cash(finance):
    await configure(finance)
    customer = (
        await finance["app"].clients.create_client(
            key="customer", actor_id=finance["actor"], name="Customer"
        )
    )["client_id"]
    await deal(finance, mode="wallet", client_id=customer)
    wallets = {
        w["instrument_code"]: w["balance"]
        for w in await finance["app"].client_queries.wallets(customer)
    }
    assert wallets["THB_CASH"] == -3600 and wallets["USDT"] == 100
    assert await balance(finance, "THB_CASH") == (0, 0)
    assert await balance(finance, "USDT") == (0, 0)
    await close(finance)
    assert await ledger(finance, "owner_profit_payable") == 1


async def test_loss_stays_in_own_month_and_empty_close(finance):
    await configure(finance)
    await fund(finance, instrument="USDT", quantity=Decimal(100))
    await deal(finance, client_rate="34")
    report = await finance["app"].profit_queries.report(PERIOD)
    assert report["net_profit_usdt"] == Decimal("-1.42857186")
    assert await ledger(finance, "platform_commission_payable") == 0
    await close(finance)
    assert await ledger(finance, "owner_profit_payable") == 0
    assert (await finance["app"].profit_queries.report(date(2026, 11, 1)))["net_profit_usdt"] == 0
    empty = await finance["app"].profit.close(
        key="empty",
        actor_id=finance["actor"],
        period=date(2026, 11, 1),
        effective_at=datetime(2026, 12, 1, tzinfo=UTC),
    )
    assert empty.mode.value == "memorandum"


async def test_profit_revision_replaces_best_change_and_keeps_snapshot(finance):
    await configure(finance)
    await fund(finance, instrument="USDT", quantity=Decimal(100))
    posted = await deal(finance)
    original_deal = (await finance["app"].profit_queries.best_change())[0]["deal_id"]
    closed = await close(finance)
    old = await finance["app"].profit_queries.closure(PERIOD)
    await finance["app"].profit.start_revision(
        **common(finance, "revision"), period=PERIOD, reason="Fix rate"
    )
    rev = (await finance["app"].profit_queries.revisions(PERIOD))[0]["id"]
    await finance["app"].profit.reverse(
        **common(finance, "undo-close"),
        operation_id=closed.id,
        reason="Fix rate",
        profit_revision_id=rev,
    )
    await finance["app"].profit.reverse(
        **common(finance, "undo-bc"),
        operation_id=posted.id,
        reason="Fix rate",
        profit_revision_id=rev,
    )
    await deal(
        finance,
        key="corrected",
        client_rate="35",
        correction_of=original_deal,
        profit_revision_id=rev,
    )
    await close(finance, key="close2", profit_revision_id=rev)
    assert await finance["app"].profit_queries.closure(PERIOD, 1) == old
    assert (await finance["app"].profit_queries.closure(PERIOD))["net_profit_usdt"] == 0
    assert await ledger(finance, "owner_profit_payable") == 0
    assert await ledger(finance, "best_change_buffer") == 0
    with pytest.raises(InvalidPosting):
        await deal(finance, key="finished-revision", profit_revision_id=rev)


async def income(finance, amount="1000"):
    from domain.finance.models import JournalLine, OperationCommand, PostingPlan, Side, Valuation

    account = await finance["pool"].fetchval(
        "SELECT id FROM ledger_accounts WHERE purpose='trading_income'"
    )
    cash = finance["cash"]["USDT"]
    command = OperationCommand(
        "income", "manual_journal", finance["actor"], DAY, INSTANT, (cash, account), "{}"
    )
    value = Decimal(amount)
    await finance["app"].posting.post(
        command,
        lambda state: PostingPlan(
            (
                JournalLine(cash, "USDT", Side.DEBIT, value, value, Valuation.BASE),
                JournalLine(account, "USDT", Side.CREDIT, value, value, Valuation.BASE),
            )
        ),
    )


async def test_profit_formula_interest_capital_loans_and_payments(finance):
    from tests.thai.test_capital import accrue, contribute

    await income(finance)
    created = await finance["app"].capital.create_agreement(
        **common(finance, "investor"), monthly_interest_percent=Decimal(2), name="Investor"
    )
    agreement = await finance["pool"].fetchval(
        "SELECT id FROM capital_agreements WHERE created_operation_id=$1", created.id
    )
    await contribute(finance, agreement, day=DAY)
    await accrue(finance, agreement, approved=Decimal(200))
    category = await finance["pool"].fetchval("SELECT min(id) FROM expense_categories")
    await finance["app"].expenses.record(
        **common(finance, "expense"),
        category_id=category,
        instrument="USDT",
        amount="100",
        quoted_rate="1",
        payer="business",
        shares={"common": "100"},
        comment="Office",
    )
    client = (
        await finance["app"].clients.create_client(
            key="lender", actor_id=finance["actor"], name="Lender", group_code="partner"
        )
    )["client_id"]
    await finance["app"].wallets.deposit(
        **common(finance, "loan"),
        client_id=client,
        instrument="USDT",
        quantity="100",
        quoted_rate="1",
    )
    report = await finance["app"].profit_queries.report(PERIOD)
    assert (
        report["income_usdt"],
        report["business_expenses_usdt"],
        report["investor_interest_usdt"],
        report["net_profit_usdt"],
    ) == (1000, 100, 200, 700)
    await close(finance)
    args = {
        **common(finance, "pay", NOV),
        "recipient": "pe",
        "instrument": "USDT",
        "quantity": "100",
    }
    payment = await finance["app"].profit.pay(**args)
    assert await finance["app"].profit.pay(**args) == payment
    assert await ledger(finance, "owner_profit_payable") == 600
    assert (await finance["app"].profit_queries.report(PERIOD))["net_profit_usdt"] == 700
    await finance["app"].capital.pay_interest(
        **common(finance, "interest-payment", NOV),
        agreement_id=agreement,
        instrument="USDT",
        quantity="200",
        reason="Manager decision",
    )
    assert (await finance["app"].profit_queries.report(PERIOD))["net_profit_usdt"] == 700
    results = await asyncio.gather(
        *(
            finance["app"].profit.pay(
                **common(finance, key, NOV), recipient="pe", instrument="USDT", quantity="200"
            )
            for key in ("a", "b")
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, InvalidPosting) for r in results) == 1
    assert await ledger(finance, "owner_profit_payable") == 400


async def test_platform_payment_storno_and_dependencies(finance):
    await configure(finance)
    await fund(finance, instrument="USDT", quantity=Decimal(101))
    posted = await deal(finance)
    paid = await finance["app"].profit.pay(
        **common(finance, "fee", NOV), recipient="platform", instrument="USDT", quantity=".5"
    )
    assert await ledger(finance, "platform_commission_payable") == Decimal(".35714286")
    with pytest.raises(ReversalBlocked):
        await finance["app"].profit.reverse(
            **common(finance, "undo"), operation_id=posted.id, reason="Fix"
        )
    await finance["app"].profit.reverse(
        **common(finance, "undo-pay", NOV), operation_id=paid.id, reason="Fix"
    )
    await finance["app"].profit.reverse(
        **common(finance, "undo-deal"), operation_id=posted.id, reason="Fix"
    )
    assert await ledger(finance, "platform_commission_payable") == 0
    assert await balance(finance, "USDT") == (101, 101)


async def test_expense_and_interest_correction_in_profit_revision(finance):
    from tests.thai.test_capital import contribute

    await income(finance)
    created = await finance["app"].capital.create_agreement(
        **common(finance, "investor"), monthly_interest_percent=Decimal(2), name="Investor"
    )
    agreement = await finance["pool"].fetchval(
        "SELECT id FROM capital_agreements WHERE created_operation_id=$1", created.id
    )
    await contribute(finance, agreement, day=DAY)
    interest = await finance["app"].capital.accrue(
        key="interest",
        actor_id=finance["actor"],
        business_date=date(2026, 10, 31),
        effective_at=datetime(2026, 11, 1, tzinfo=UTC),
        agreement_id=agreement,
        period=PERIOD,
        approved_usdt="200",
        reason="Manager",
    )
    category = await finance["pool"].fetchval("SELECT min(id) FROM expense_categories")
    expense = await finance["app"].expenses.record(
        **common(finance, "expense"),
        category_id=category,
        instrument="USDT",
        amount="100",
        quoted_rate="1",
        payer="pe",
        shares={"common": "100"},
    )
    expense_id = await finance["pool"].fetchval(
        "SELECT id FROM expenses WHERE operation_id=$1", expense.id
    )
    await finance["app"].expenses.close(
        key="expense-close", actor_id=finance["actor"], period=PERIOD, effective_at=INSTANT
    )
    closed = await close(finance)
    old = await finance["app"].profit_queries.closure(PERIOD)
    old_expense = await finance["app"].expense_queries.statement(PERIOD)
    await finance["app"].profit.start_revision(
        **common(finance, "revision"), period=PERIOD, reason="Manager correction"
    )
    revision = (await finance["app"].profit_queries.revisions(PERIOD))[0]["id"]
    await finance["app"].expenses.start_revision(
        **common(finance, "expense-revision"),
        period=PERIOD,
        reason="Fix",
        profit_revision_id=revision,
    )
    expense_revision = (await finance["app"].expense_queries.revisions(PERIOD))[0]["id"]
    await finance["app"].profit.reverse(
        **common(finance, "undo-close"),
        operation_id=closed.id,
        reason="Fix",
        profit_revision_id=revision,
    )
    await finance["app"].expenses.reverse(
        **common(finance, "undo-expense"),
        operation_id=expense.id,
        reason="Fix",
        revision_id=expense_revision,
        profit_revision_id=revision,
    )
    await finance["app"].capital.reverse(
        **common(finance, "undo-interest", date(2026, 10, 31)),
        agreement_id=agreement,
        original_id=interest.id,
        reason="Fix",
        profit_revision_id=revision,
    )
    await finance["app"].capital.accrue(
        key="interest2",
        actor_id=finance["actor"],
        business_date=date(2026, 10, 31),
        effective_at=datetime(2026, 11, 1, tzinfo=UTC),
        agreement_id=agreement,
        period=PERIOD,
        approved_usdt="150",
        reason="Manager correction",
        profit_revision_id=revision,
    )
    await finance["app"].expenses.record(
        **common(finance, "expense2"),
        category_id=category,
        instrument="USDT",
        amount="50",
        quoted_rate="1",
        payer="pe",
        shares={"common": "100"},
        revision_id=expense_revision,
        profit_revision_id=revision,
        correction_of=expense_id,
    )
    await finance["app"].expenses.close(
        key="expense-close2",
        actor_id=finance["actor"],
        period=PERIOD,
        effective_at=INSTANT,
        revision_id=expense_revision,
        profit_revision_id=revision,
    )
    await close(finance, key="close2", profit_revision_id=revision)
    assert (await finance["app"].profit_queries.closure(PERIOD))["net_profit_usdt"] == 800
    assert await finance["app"].profit_queries.closure(PERIOD, 1) == old
    assert await finance["app"].expense_queries.statement(PERIOD, 1) == old_expense


async def test_terms_permissions_payout_and_frozen_closure(finance):
    from domain.finance.models import PermissionDenied

    await income(finance)
    await finance["app"].profit.set_terms(
        **common(finance, "terms"),
        valid_from=DAY,
        skyex_percent="60",
        pe_percent="40",
        reason="Agreement",
    )
    closed = await close(finance)
    old = await finance["app"].profit_queries.closure(PERIOD)
    assert sorted(a["amount_usdt"] for a in old["accruals"]) == [400, 600]
    await finance["app"].profit.pay(
        **common(finance, "pay", NOV), recipient="pe", instrument="USDT", quantity="100"
    )
    await finance["app"].profit.start_revision(
        **common(finance, "revision"), period=PERIOD, reason="Recheck"
    )
    revision = (await finance["app"].profit_queries.revisions(PERIOD))[0]["id"]
    with pytest.raises(ReversalBlocked):
        await finance["app"].profit.reverse(
            **common(finance, "undo"),
            operation_id=closed.id,
            reason="Recheck",
            profit_revision_id=revision,
        )
    await finance["pool"].execute(
        "WITH removed AS (DELETE FROM user_roles WHERE user_id=$1 AND role='owner') UPDATE user_roles SET role='accountant' WHERE user_id=$1 AND role='manager'", finance["actor"]
    )
    with pytest.raises(PermissionDenied):
        await finance["app"].profit.set_terms(
            **common(finance, "unauthorized", NOV),
            valid_from=NOV,
            skyex_percent="50",
            pe_percent="50",
            reason="No",
        )
    with pytest.raises(InvalidPosting):
        await finance["app"].profit.pay(
            **common(finance, "missing-rate", NOV),
            recipient="pe",
            instrument="THB_CASH",
            quantity="1",
        )
    assert await finance["app"].profit_queries.closure(PERIOD) == old


async def test_profit_api_and_telegram_delivery(finance):
    from types import SimpleNamespace

    from httpx import ASGITransport, AsyncClient

    from api.auth import create_access_token
    from api.thai_finance import create_thai_finance_api
    from app.thai_telegram import ThaiTelegramCommands, ThaiTelegramDelivery
    from config import Config
    from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository

    buffer, dany = await configure(finance)
    await finance["app"].clients.bind_chat(
        key="bind-buffer", actor_id=finance["actor"], client_id=buffer, chat_id=-100
    )
    await finance["app"].clients.bind_chat(
        key="bind-dany", actor_id=finance["actor"], client_id=dany, chat_id=-200
    )
    await fund(finance, instrument="USDT", quantity=Decimal(100))
    app = create_thai_finance_api(container=finance["app"], jwt_secret="x" * 32)
    headers = {
        "Authorization": "Bearer "
        + create_access_token(tg_user_id=12345, secret="x" * 32, ttl_seconds=60)
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    ) as http:
        payload = {
            "idempotency_key": "api-bc",
            "effective_at": INSTANT.isoformat(),
            "operation": "sale",
            "quantity": "100",
            "market_rate": "35",
            "client_rate": "36",
        }
        posted = await http.post("/api/v1/best-change/deals", json=payload)
        assert posted.status_code == 200, posted.text
        assert (await http.post("/api/v1/best-change/deals", json=payload)).json() == posted.json()
        report = await http.get("/api/v1/profit/report", params={"period": str(PERIOD)})
        assert Decimal(report.json()["net_profit_usdt"]) == 1
        closed = await http.post(
            "/api/v1/profit/close",
            json={
                "idempotency_key": "api-close",
                "effective_at": datetime(2026, 11, 1, tzinfo=UTC).isoformat(),
                "period": str(PERIOD),
            },
        )
        assert closed.status_code == 200, closed.text
        assert len((await http.get("/api/v1/profit/obligations")).json()["owners"]) == 2
    commands = ThaiTelegramCommands(finance["app"], Config("unused", "x" * 32))
    result = await commands.execute(
        telegram_user_id=12345,
        chat_id=-100,
        message_id=10,
        text="/бест отчет 10.2026",
        effective_at=INSTANT,
    )
    assert "Доля Данияра: 1" in result

    class Bot:
        calls = []

        async def send_message(self, chat_id, text, **kwargs):
            self.calls.append(chat_id)
            return SimpleNamespace(message_id=1)

        async def edit_message_text(self, *args, **kwargs):
            pass

    bot = Bot()
    delivery = ThaiTelegramDelivery(finance["app"], bot)
    outbox = OutboxDeliveryRepository(finance["pool"])
    for _ in range(20):
        event = await outbox.claim()
        if event is None:
            break
        await delivery.deliver(event)
        await delivery.deliver(event)
        await outbox.delivered(event)
    assert bot.calls.count(-100) == 2
    assert bot.calls.count(-200) == 1
