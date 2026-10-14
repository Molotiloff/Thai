import asyncio
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from domain.finance.models import InvalidPosting, PeriodClosed, PermissionDenied, ReversalBlocked
from tests.thai.test_posting import DAY, INSTANT, balance, fund

PERIOD = date(2026, 10, 1)


def common(finance, key, day=DAY):
    return {"key": key, "actor_id": finance["actor"], "business_date": day, "effective_at": INSTANT}


async def record(
    finance, key="expense", payer="pe", amount="1000", shares=None, rate="35", **extra
):
    category = await finance["pool"].fetchval("SELECT min(id) FROM expense_categories")
    return await finance["app"].expenses.record(
        **common(finance, key),
        category_id=category,
        instrument="THB_CASH",
        amount=amount,
        quoted_rate=rate,
        payer=payer,
        shares=shares or {"daniyar": "20", "common": "80"},
        comment="Office",
        **extra,
    )


async def totals(finance):
    rows = (await finance["app"].expense_queries.debts())["totals"]
    parties = {
        row["party_id"]: row["code"]
        for row in await finance["pool"].fetch("SELECT * FROM business_participants")
    }
    return {
        (
            parties[row["debtor_party_id"]],
            parties[row["creditor_party_id"]],
            row["currency_code"],
        ): row["amount"]
        for row in rows
    }


async def ledger(finance, purpose):
    return await finance["pool"].fetchval(
        "SELECT coalesce(sum(b.balance),0) FROM posted_account_balances b JOIN ledger_accounts a ON a.id=b.account_id WHERE a.purpose=$1",
        purpose,
    )


async def pay(
    finance,
    key="pay",
    amount="50",
    debtor="daniyar",
    creditor="pe",
    method="direct",
    day=DAY,
    **extra,
):
    return await finance["app"].expenses.settle(
        **common(finance, key, day),
        debtor=debtor,
        creditor=creditor,
        currency="THB",
        amount=amount,
        method=method,
        **extra,
    )


async def test_personal_owner_expense_debts_and_no_auto_wallet_or_cash(finance):
    dany = await finance["pool"].fetchval(
        "SELECT party_id FROM business_participants WHERE code='daniyar'"
    )
    client = await finance["app"].clients.create_client(
        key="client", actor_id=finance["actor"], party_id=dany
    )
    op = await record(finance)
    assert await record(finance) == op
    assert await totals(finance) == {("daniyar", "pe", "THB"): 200, ("skyex", "pe", "THB"): 400}
    assert await ledger(finance, "operating_expense") == Decimal("22.85714286")
    assert await ledger(finance, "personal_expense_funding") == Decimal("22.85714286")
    assert await balance(finance, "THB_CASH") == (0, 0)
    assert all(
        row["balance"] == 0
        for row in await finance["app"].client_queries.wallets(client["client_id"])
    )
    await pay(finance)
    assert (await totals(finance))[("daniyar", "pe", "THB")] == 150
    assert await ledger(finance, "operating_expense") == Decimal("22.85714286")
    with pytest.raises(ReversalBlocked):
        await finance["app"].expenses.reverse(
            **common(finance, "blocked"), operation_id=op.id, reason="Correction"
        )


async def test_cash_expense_partial_historical_payment_and_reversal(finance):
    await fund(finance, quantity=Decimal(1000))
    original = await record(finance, payer="business")
    assert await balance(finance, "THB_CASH") == (0, 0)
    assert await totals(finance) == {("daniyar", "business", "THB"): 200}
    assert await ledger(finance, "operating_expense") == Decimal("22.85714286")
    paid = await pay(
        finance,
        amount="50",
        creditor="business",
        method="cash",
        instrument="THB_CASH",
        quoted_rate="40",
    )
    assert await balance(finance, "THB_CASH") == (50, Decimal("1.25"))
    assert await ledger(finance, "expense_receivable") == 150
    await pay(
        finance,
        key="remaining",
        amount="150",
        creditor="business",
        method="cash",
        instrument="THB_BANK",
        quoted_rate="40",
    )
    assert await totals(finance) == {}
    assert await ledger(finance, "expense_receivable") == 0
    assert (
        await finance["pool"].fetchval(
            "SELECT sum(carrying_usdt) FROM posted_account_balances b JOIN ledger_accounts a ON a.id=b.account_id WHERE a.purpose='expense_receivable'"
        )
        == 0
    )
    assert await ledger(finance, "operating_expense") == Decimal("22.85714286")
    await finance["app"].expenses.reverse(
        **common(finance, "undo-pay"), operation_id=paid.id, reason="Correction"
    )
    assert await totals(finance) == {("daniyar", "business", "THB"): 50}
    with pytest.raises(ReversalBlocked):
        await finance["app"].expenses.reverse(
            **common(finance, "undo-original"), operation_id=original.id, reason="Still paid"
        )


async def test_mixed_shares_and_correction_link(finance):
    op = await record(finance, shares={"daniyar": "20", "pe": "10", "skyex": "30", "common": "40"})
    assert await totals(finance) == {("daniyar", "pe", "THB"): 200, ("skyex", "pe", "THB"): 500}
    await finance["app"].expenses.reverse(
        **common(finance, "undo"), operation_id=op.id, reason="Correction"
    )
    assert await totals(finance) == {}
    eid = await finance["pool"].fetchval("SELECT id FROM expenses WHERE operation_id=$1", op.id)
    changed = await record(finance, key="corrected", amount="2000", correction_of=eid)
    assert await ledger(finance, "operating_expense") == Decimal("45.71428571")
    assert (await finance["app"].expense_queries.detail(eid))["corrections"][0][
        "operation_id"
    ] == changed.id


async def test_netting_and_fifo_concurrent_payments(finance):
    await record(finance)
    await record(
        finance, key="opposite", payer="skyex", amount="250", shares={"pe": "40", "skyex": "60"}
    )
    net = await pay(
        finance, key="net", debtor="skyex", creditor="pe", amount="100", method="netting"
    )
    assert await totals(finance) == {("daniyar", "pe", "THB"): 200, ("skyex", "pe", "THB"): 300}
    assert await ledger(finance, "operating_expense") == Decimal("30.00000000")
    await finance["app"].expenses.reverse(
        **common(finance, "undo-net"), operation_id=net.id, reason="Correction"
    )
    assert (await totals(finance))[("pe", "skyex", "THB")] == 100
    results = await asyncio.gather(
        pay(finance, key="p1", amount="150"),
        pay(finance, key="p2", amount="150"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, InvalidPosting) for result in results) == 1
    assert (await totals(finance))[("daniyar", "pe", "THB")] == 50


async def test_explicit_wallet_settlement_and_negative_balance(finance):
    await fund(finance, quantity=Decimal(1000))
    await record(finance, payer="business")
    dany = await finance["pool"].fetchval(
        "SELECT party_id FROM business_participants WHERE code='daniyar'"
    )
    cid = (
        await finance["app"].clients.create_client(
            key="client", actor_id=finance["actor"], party_id=dany
        )
    )["client_id"]
    paid = await pay(
        finance,
        amount="200",
        creditor="business",
        method="wallet",
        instrument="THB_CASH",
        quoted_rate="35",
        client_id=cid,
    )
    assert (
        await pay(
            finance,
            amount="200",
            creditor="business",
            method="wallet",
            instrument="THB_CASH",
            quoted_rate="35",
            client_id=cid,
        )
        == paid
    )
    assert await balance(finance, "THB_CASH") == (0, 0)
    wallet = next(
        row
        for row in await finance["app"].client_queries.wallets(cid)
        if row["instrument_code"] == "THB_CASH"
    )
    assert wallet["balance"] == -200
    assert await totals(finance) == {}


async def test_month_close_carryforward_storno_and_owner_revision(finance):
    await record(finance)
    await pay(finance)
    close = await finance["app"].expenses.close(
        key="close", actor_id=finance["actor"], period=PERIOD, effective_at=INSTANT
    )
    assert (
        await finance["app"].expenses.close(
            key="close", actor_id=finance["actor"], period=PERIOD, effective_at=INSTANT
        )
        == close
    )
    frozen = await finance["app"].expense_queries.statement(PERIOD)
    assert frozen["closed"] and frozen["revision"] == 1
    row = next(row for row in frozen["rows"] if row["accrued"] == 200)
    assert (row["opening"], row["accrued"], row["settled"], row["closing"]) == (0, 200, 50, 150)
    with pytest.raises(PeriodClosed):
        await record(finance, key="closed")
    with pytest.raises(PeriodClosed):
        await pay(finance, key="closed-pay")
    next_month = await finance["app"].expense_queries.statement(date(2026, 11, 1))
    row = next(row for row in next_month["rows"] if row["opening"] == 150)
    assert row["closing"] == 150
    # Opening a revision does not reopen the original month for ordinary manager commands.
    await finance["app"].expenses.start_revision(
        **common(finance, "rev"), period=PERIOD, reason="Fix payment"
    )
    rev = (await finance["app"].expense_queries.revisions(PERIOD))[0]["id"]
    await pay(finance, key="revision-pay", amount="50", revision_id=rev)
    with pytest.raises(PeriodClosed):
        await pay(finance, key="no-rev-id", amount="50")
    await finance["app"].expenses.close(
        key="revision-close",
        actor_id=finance["actor"],
        period=PERIOD,
        effective_at=INSTANT,
        revision_id=rev,
    )
    revised = await finance["app"].expense_queries.statement(PERIOD)
    assert revised["revision"] == 2
    assert await finance["app"].expense_queries.statement(PERIOD, revision=1) == frozen
    assert next(row for row in revised["rows"] if row["accrued"] == 200)["closing"] == 100
    with pytest.raises(PeriodClosed):
        await pay(finance, key="completed-rev", amount="50", revision_id=rev)


async def test_non_owner_financing_clears_payable_without_new_expense(finance):
    await record(finance, payer="daniyar")
    assert await ledger(finance, "personal_expense_payable") == Decimal("22.85714286")
    await pay(finance, debtor="pe", creditor="daniyar", amount="400")
    assert await ledger(finance, "personal_expense_payable") == Decimal("11.42857143")
    assert await ledger(finance, "operating_expense") == Decimal("22.85714286")
    await fund(finance, quantity=Decimal(400))
    await pay(
        finance,
        key="cash-payment",
        debtor="skyex",
        creditor="daniyar",
        amount="400",
        method="cash",
        instrument="THB_CASH",
        quoted_rate="35",
    )
    assert await ledger(finance, "personal_expense_payable") == 0
    assert await balance(finance, "THB_CASH") == (0, 0)


async def test_expenses_api_decimal_validation_and_roles(finance):
    api = create_thai_finance_api(container=finance["app"], jwt_secret="x" * 32)
    headers = {
        "Authorization": "Bearer "
        + create_access_token(tg_user_id=12345, secret="x" * 32, ttl_seconds=60)
    }
    async with AsyncClient(
        transport=ASGITransport(app=api), base_url="http://test", headers=headers
    ) as http:
        categories = (await http.get("/api/v1/expense-categories")).json()
        payload = {
            "idempotency_key": "api-expense",
            "effective_at": INSTANT.isoformat(),
            "category_id": categories[0]["id"],
            "instrument": "THB_CASH",
            "amount": "1000",
            "quoted_rate": "35",
            "payer": "pe",
            "shares": {"daniyar": "20", "common": "80"},
        }
        response = await http.post("/api/v1/expenses", json=payload)
        assert response.status_code == 200, response.text
        assert (await http.post("/api/v1/expenses", json=payload)).json() == response.json()
        assert (await http.get("/api/v1/expense-debts")).status_code == 200
        assert (
            await http.post(
                "/api/v1/expenses", json={**payload, "idempotency_key": "invalid", "amount": 1000.1}
            )
        ).status_code == 422
        assert (
            await http.post(
                "/api/v1/expenses", json={**payload, "idempotency_key": "deposit", "deposit": True}
            )
        ).status_code == 422
    await finance["pool"].execute(
        "DELETE FROM user_roles WHERE user_id=$1 AND role='owner'", finance["actor"]
    )
    await finance["app"].expenses.close(
        key="manager-close", actor_id=finance["actor"], period=PERIOD, effective_at=INSTANT
    )
    with pytest.raises(PermissionDenied):
        await finance["app"].expenses.start_revision(
            **common(finance, "manager-rev"), period=PERIOD, reason="Denied"
        )


async def test_tiny_nonowner_claim_can_be_completely_paid_with_zero_carrying_remainder(finance):
    await record(finance, payer="daniyar", amount=".02", shares={"pe": "100"}, rate="2000000")
    await pay(finance, key="one", debtor="pe", creditor="daniyar", amount=".01")
    second = await pay(finance, key="two", debtor="pe", creditor="daniyar", amount=".01")
    assert await pay(finance, key="two", debtor="pe", creditor="daniyar", amount=".01") == second
    assert await ledger(finance, "personal_expense_payable") == 0
    assert await totals(finance) == {}


async def test_reverse_closed_expense_with_revision_and_exact_frozen_history(finance):
    original = await record(finance)
    await finance["app"].expenses.close(
        key="close", actor_id=finance["actor"], period=PERIOD, effective_at=INSTANT
    )
    before = await finance["app"].expense_queries.statement(PERIOD, 1)
    await finance["app"].expenses.start_revision(
        **common(finance, "revision"), period=PERIOD, reason="Wrong amount"
    )
    revision = (await finance["app"].expense_queries.revisions(PERIOD))[0]["id"]
    await finance["app"].expenses.reverse(
        **common(finance, "undo"),
        operation_id=original.id,
        reason="Wrong amount",
        revision_id=revision,
    )
    assert await totals(finance) == {}
    await finance["app"].expenses.close(
        key="close-revision",
        actor_id=finance["actor"],
        period=PERIOD,
        effective_at=INSTANT,
        revision_id=revision,
    )
    assert await finance["app"].expense_queries.statement(PERIOD, 1) == before
    revised = await finance["app"].expense_queries.statement(PERIOD)
    assert all(row["closing"] == 0 for row in revised["rows"])
    assert sum(row["corrected"] for row in revised["rows"]) == -600


async def test_two_closes_and_close_payment_race_are_serialized(finance):
    await record(finance)
    closes = await asyncio.gather(
        *(
            finance["app"].expenses.close(
                key="close", actor_id=finance["actor"], period=PERIOD, effective_at=INSTANT
            )
            for _ in range(3)
        )
    )
    assert len({op.id for op in closes}) == 1
    assert await finance["pool"].fetchval("SELECT count(*) FROM expense_period_closures") == 1
    # The next month payment uses the original debt; no duplicate carryforward obligation.
    paid = await pay(finance, key="nov-payment", amount="50", day=date(2026, 11, 2))
    november = await finance["app"].expense_queries.statement(date(2026, 11, 1))
    assert next(row for row in november["rows"] if row["opening"] == 200)["closing"] == 150
    assert await finance["pool"].fetchval("SELECT count(*) FROM expense_obligations") == 2
    assert await pay(finance, key="nov-payment", amount="50", day=date(2026, 11, 2)) == paid


async def test_fifo_across_expenses_historical_quotes_and_wrong_wallet_rollback(finance):
    await fund(finance, quantity=Decimal(2000))
    first = await record(finance, payer="business")
    second = await record(finance, key="second", payer="business", rate="40")
    await finance["app"].trading.set_rate(
        **common(finance, "market"), instrument="THB_CASH", quoted_rate="50"
    )
    first_id = await finance["pool"].fetchval(
        "SELECT id FROM expenses WHERE operation_id=$1", first.id
    )
    assert (await finance["app"].expense_queries.detail(first_id))["quoted_rate"] == 35
    unrelated = (
        await finance["app"].clients.create_client(
            key="client", actor_id=finance["actor"], name="Unrelated"
        )
    )["client_id"]
    with pytest.raises(InvalidPosting):
        await pay(
            finance,
            key="wrong-wallet",
            amount="100",
            creditor="business",
            method="wallet",
            instrument="THB_CASH",
            quoted_rate="50",
            client_id=unrelated,
        )
    assert await ledger(finance, "expense_receivable") == 400
    op = await pay(
        finance,
        key="cash-pay",
        amount="300",
        creditor="business",
        method="cash",
        instrument="THB_CASH",
        quoted_rate="50",
    )
    resolution = await finance["pool"].fetchval(
        "SELECT id FROM expense_resolutions WHERE operation_id=$1", op.id
    )
    allocated = (await finance["app"].expense_queries.resolution(resolution))["allocations"]
    assert [row["amount"] for row in sorted(allocated, key=lambda row: row["obligation_id"])] == [
        200,
        100,
    ]
    assert sum(row["amount_usdt"] for row in allocated) == Decimal("8.21428571")
    assert await ledger(finance, "expense_receivable") == 100
    assert (
        await finance["pool"].fetchval(
            "SELECT amount_usdt FROM expenses WHERE operation_id=$1", second.id
        )
        == 25
    )


async def test_netting_with_business_payable_is_no_second_expense(finance):
    await record(finance)
    await record(finance, key="dany", payer="daniyar", amount="500", shares={"pe": "100"})
    before = await ledger(finance, "operating_expense")
    await pay(finance, key="net", debtor="daniyar", creditor="pe", amount="200", method="netting")
    assert await totals(finance) == {("skyex", "pe", "THB"): 400, ("pe", "daniyar", "THB"): 300}
    assert await ledger(finance, "operating_expense") == before


async def test_zero_business_share_and_deposit_leave_no_business_expense(finance):
    await record(finance, shares={"daniyar": "100"})
    assert await ledger(finance, "operating_expense") == 0
    assert await totals(finance) == {("daniyar", "pe", "THB"): 1000}
    with pytest.raises(InvalidPosting):
        await record(finance, key="deposit", deposit=True)
    with pytest.raises(InvalidPosting):
        await record(finance, key="bad-shares", shares={"common": "99"})
    assert await finance["pool"].fetchval("SELECT count(*) FROM expenses") == 1


async def test_expense_revision_scope_permissions_and_stale_later_snapshot(finance):
    await record(finance)
    await finance["app"].expenses.close(
        key="oct-close", actor_id=finance["actor"], period=PERIOD, effective_at=INSTANT
    )
    november = date(2026, 11, 1)
    await finance["app"].expenses.close(
        key="nov-close", actor_id=finance["actor"], period=november, effective_at=INSTANT
    )
    await finance["app"].expenses.start_revision(
        **common(finance, "rev"), period=PERIOD, reason="Correction"
    )
    revision = (await finance["app"].expense_queries.revisions(PERIOD))[0]["id"]
    with pytest.raises(InvalidPosting):
        await pay(
            finance, key="wrong-month", amount="50", day=date(2026, 12, 1), revision_id=revision
        )
    await finance["pool"].execute(
        "DELETE FROM user_roles WHERE user_id=$1 AND role='owner'", finance["actor"]
    )
    with pytest.raises(PermissionDenied):
        await pay(finance, key="no-right", amount="50", revision_id=revision)
    await finance["pool"].execute("INSERT INTO user_roles VALUES ($1,'owner')", finance["actor"])
    await pay(finance, key="rev-pay", amount="50", revision_id=revision)
    await finance["app"].expenses.close(
        key="oct-reclose",
        actor_id=finance["actor"],
        period=PERIOD,
        effective_at=INSTANT,
        revision_id=revision,
    )
    assert (await finance["app"].expense_queries.statement(november))["needs_revision"]


async def test_month_close_and_same_month_payment_cannot_produce_inconsistent_statement(finance):
    await record(finance)
    result = await asyncio.gather(
        finance["app"].expenses.close(
            key="close-race", actor_id=finance["actor"], period=PERIOD, effective_at=INSTANT
        ),
        pay(finance, key="payment-race", amount="50"),
        return_exceptions=True,
    )
    assert not isinstance(result[0], Exception)
    statement = await finance["app"].expense_queries.statement(PERIOD)
    row = next(row for row in statement["rows"] if row["accrued"] == 200)
    expected = 200 if isinstance(result[1], PeriodClosed) else 150
    assert row["closing"] == expected
    assert (await totals(finance))[("daniyar", "pe", "THB")] == expected


async def test_expense_query_distinguishes_business_cost_and_signed_current_debts(finance):
    op = await record(finance)
    eid = await finance["pool"].fetchval("SELECT id FROM expenses WHERE operation_id=$1", op.id)
    detail = await finance["app"].expense_queries.detail(eid)
    assert detail["business_amount"] == 800
    assert detail["business_usdt"] == Decimal("22.85714286")
    assert detail["amount_usdt"] == Decimal("28.57142857")
    debts = await finance["app"].expense_queries.debts()
    pe = next(row for row in debts["by_participant"] if row["name"] == "ПЕ")
    assert (pe["payable"], pe["receivable"], pe["net_payable"]) == (0, 600, -600)
