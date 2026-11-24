"""Expense financing through existing partner wallets, separate from capital."""
import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from app.thai_telegram import ThaiTelegramDelivery
from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository
from domain.finance.models import (
    IdempotencyConflict,
    InsufficientCash,
    InvalidPosting,
    PeriodClosed,
    PermissionDenied,
)
from tests.thai.test_posting import DAY, INSTANT, balance, fund

PERIOD = date(2026, 10, 1)


def common(finance, key, day=DAY):
    return {"key": key, "actor_id": finance["actor"], "business_date": day, "effective_at": INSTANT}


async def bind_wallets(finance):
    pool, app = finance["pool"], finance["app"]
    result = {}
    for code in ("skyex", "pe", "daniyar"):
        client = await pool.fetchval("SELECT client_id FROM business_client_bindings WHERE purpose=$1", code + "_balance")
        if client is None:
            client = (await app.clients.create_client(key="expense-client-"+code,
                actor_id=finance["actor"], name=code, group_code="partner" if code != "daniyar" else "balance"))["client_id"]
            if code == "daniyar":
                await pool.execute("INSERT INTO business_client_bindings VALUES ('daniyar_balance',$1)", client)
            else:
                await app.expenses.bind_participant(key="expense-bind-"+code, actor_id=finance["actor"], code=code, client_id=client)
        result[code] = client
    return result


async def record(finance, key="expense", payer="pe", amount="1000", shares=None, rate="35", instrument="THB_CASH", **extra):
    await bind_wallets(finance)
    return await finance["app"].expenses.record(**common(finance,key),
        category_id=await finance["pool"].fetchval("SELECT min(id) FROM expense_categories"),
        instrument=instrument, amount=amount, quoted_rate=rate, payer=payer,
        shares=shares or {"daniyar":"20","common":"80"}, comment="Office", **extra)


async def totals(finance):
    rows = (await finance["app"].expense_queries.debts())["wallet_balances"]
    return {row["code"]: row["wallet_balance"] for row in rows if row["wallet_balance"]}


async def ledger(finance, purpose):
    return await finance["pool"].fetchval("SELECT coalesce(sum(b.balance),0) FROM posted_account_balances b JOIN ledger_accounts a ON a.id=b.account_id WHERE a.purpose=$1",purpose)


async def pay(finance, key="pay", amount="10", recipient="pe", day=DAY):
    clients = await bind_wallets(finance)
    return await finance["app"].wallets.withdraw(**common(finance,key,day),
        client_id=clients[recipient], instrument="USDT", quantity=amount)


@pytest.mark.parametrize("payer",["business","skyex","pe","daniyar"])
@pytest.mark.parametrize("daniyar_share",["0","20","100"])
@pytest.mark.parametrize("instrument,amount,rate",[("USDT","1000",None),("THB_CASH","33000","33"),("THB_BANK","33000","33"),("USD_WH","1000","1")])
async def test_payer_share_currency_matrix(finance,payer,daniyar_share,instrument,amount,rate):
    await bind_wallets(finance)
    if payer == "business":
        await finance["app"].manual_cash.adjust(**common(finance,"fund"), instrument=instrument,
            quantity=amount,chat_id=-500,comment="Initial cash")
    old = await balance(finance,instrument)
    op = await record(finance,payer=payer,shares={"daniyar":daniyar_share,"common":str(100-int(daniyar_share))},
        instrument=instrument,amount=amount,rate=rate)
    expected = {payer: Decimal(1000)} if payer != "business" else {}
    d = Decimal(daniyar_share)*10
    expected["daniyar"] = expected.get("daniyar",Decimal(0))-d
    assert await totals(finance) == {k:v for k,v in expected.items() if v}
    assert (await balance(finance,instrument))[0] == old[0]-(Decimal(amount) if payer=="business" else 0)
    assert await ledger(finance,"operating_expense") == Decimal(1000)-d
    assert await ledger(finance,"personal_expense_funding") == 0
    assert await finance["pool"].fetchval("SELECT count(*) FROM expense_obligations") == 0
    assert await finance["pool"].fetchval("SELECT count(*) FROM capital_contributions") == 0
    assert await record(finance,payer=payer,shares={"daniyar":daniyar_share,"common":str(100-int(daniyar_share))},instrument=instrument,amount=amount,rate=rate) == op
    await finance["app"].expenses.reverse(**common(finance,"undo"),operation_id=op.id,reason="Correction")
    assert await totals(finance) == {}
    assert await ledger(finance,"operating_expense") == 0


async def test_equal_thirds_precision_does_not_leave_a_hidden_debt(finance):
    await record(finance,instrument="USDT",rate=None,equal_split=True)
    values=await totals(finance)
    assert values["pe"] == 1000
    assert abs(values["daniyar"]) in (Decimal("333.333333"),Decimal("333.333334"))
    assert await ledger(finance,"operating_expense") == 1000+values["daniyar"]
    assert await finance["pool"].fetchval("SELECT sum(amount_usdt) FROM expense_allocations") == 1000


async def test_ordinary_wallet_repayment_updates_statement_without_second_expense(finance):
    await record(finance,instrument="USDT",rate=None,shares={"common":"100"})
    await fund(finance,instrument="USDT",quantity=Decimal(1000))
    before = await ledger(finance,"operating_expense")
    first=await pay(finance,amount="400")
    assert await pay(finance,amount="400") == first
    assert (await totals(finance))["pe"] == 600
    statement=await finance["app"].expense_queries.statement(PERIOD)
    row=next(row for row in statement["rows"] if row["accrued"]==1000)
    assert (row["accrued"],row["settled"],row["closing"]) == (1000,400,600)
    assert await ledger(finance,"operating_expense") == before
    await pay(finance,key="remainder",amount="600")
    assert await totals(finance) == {}
    assert await finance["pool"].fetchval("SELECT count(*) FROM expenses") == 1


async def test_negative_daniyar_statement_and_other_wallet_income(finance):
    clients=await bind_wallets(finance)
    await record(finance,instrument="USDT",rate=None,shares={"daniyar":"100"})
    await finance["app"].wallets.deposit(**common(finance,"dany-deposit"),client_id=clients["daniyar"],instrument="USDT",quantity="100")
    await finance["app"].expenses.close(key="close",actor_id=finance["actor"],period=PERIOD,effective_at=INSTANT)
    result=await finance["app"].expense_queries.statement(PERIOD)
    row=next(row for row in result["rows"] if row["withheld"]==1000)
    assert (row["closing"],row["other_movements"]) == (-900,100)
    assert (await totals(finance))["daniyar"] == -900
    with pytest.raises(PeriodClosed):
        await record(finance,key="closed")


async def test_binding_validation_no_guessed_clients_and_no_duplicate_wallets(finance):
    category=await finance["pool"].fetchval("SELECT min(id) FROM expense_categories")
    with pytest.raises(InvalidPosting,match="кошелёк"):
        await finance["app"].expenses.record(**common(finance,"missing"),category_id=category,
            instrument="USDT",amount="1000",payer="pe",shares={"common":"100"})
    assert await finance["pool"].fetchval("SELECT count(*) FROM expenses") == 0
    assert await finance["pool"].fetchval("SELECT count(*) FROM clients") == 0
    clients=await bind_wallets(finance)
    before=await finance["pool"].fetchval("SELECT count(*) FROM client_wallet_accounts")
    await finance["app"].expenses.bind_participant(key="same",actor_id=finance["actor"],code="pe",client_id=clients["pe"])
    assert await finance["pool"].fetchval("SELECT count(*) FROM client_wallet_accounts") == before
    with pytest.raises(InvalidPosting):
        await finance["app"].expenses.bind_participant(key="wrong",actor_id=finance["actor"],code="pe",client_id=clients["skyex"])
    await finance["app"].clients.assign_group(key="change",actor_id=finance["actor"],client_id=clients["pe"],group_code="balance",reason="Test")
    with pytest.raises(InvalidPosting):
        await record(finance)


@pytest.mark.parametrize("extra",[{"rate":"0"},{"rate":"-1"},{"rate":None},{"amount":"-1"},{"shares":{"common":"99"}},{"deposit":True}])
async def test_invalid_expense_is_atomic(finance,extra):
    with pytest.raises(InvalidPosting):
        await record(finance,**extra)
    assert await finance["pool"].fetchval("SELECT count(*) FROM expenses") == 0
    assert await totals(finance) == {}


async def test_insufficient_cash_and_conflicting_retry_leave_wallets_untouched(finance):
    with pytest.raises(InsufficientCash):
        await record(finance,payer="business")
    assert await totals(finance) == {}
    await record(finance)
    before=await totals(finance)
    with pytest.raises(IdempotencyConflict):
        await record(finance,amount="1001")
    assert await totals(finance)==before


async def test_concurrent_posting_is_exactly_once_and_permissions_are_checked(finance):
    await bind_wallets(finance)
    first,second=await asyncio.gather(record(finance),record(finance))
    assert first==second
    assert await finance["pool"].fetchval("SELECT count(*) FROM expenses") == 1
    await finance["pool"].execute("DELETE FROM user_roles WHERE user_id=$1",finance["actor"])
    with pytest.raises(PermissionDenied):
        await record(finance,key="forbidden")


async def test_correction_and_month_revision_keep_original_snapshot(finance):
    original=await record(finance,instrument="USDT",rate=None)
    eid=await finance["pool"].fetchval("SELECT id FROM expenses WHERE operation_id=$1",original.id)
    await finance["app"].expenses.close(key="close",actor_id=finance["actor"],period=PERIOD,effective_at=INSTANT)
    old=await finance["app"].expense_queries.statement(PERIOD)
    await finance["app"].expenses.start_revision(**common(finance,"revision"),period=PERIOD,reason="Fix")
    revision=(await finance["app"].expense_queries.revisions(PERIOD))[0]["id"]
    await finance["app"].expenses.reverse(**common(finance,"undo"),operation_id=original.id,reason="Fix",revision_id=revision)
    await record(finance,key="replacement",instrument="USDT",rate=None,amount="500",revision_id=revision,correction_of=eid)
    await finance["app"].expenses.close(key="reclose",actor_id=finance["actor"],period=PERIOD,effective_at=INSTANT,revision_id=revision)
    assert (await finance["app"].expense_queries.statement(PERIOD,revision=1))["rows"] == old["rows"]
    assert await totals(finance) == {"pe":500,"daniyar":-100}


async def test_api_bindings_usdt_default_rate_and_float_rejection(finance):
    clients=await bind_wallets(finance)
    api=create_thai_finance_api(container=finance["app"],jwt_secret=finance["config"].jwt_secret)
    token=create_access_token(tg_user_id=12345,secret=finance["config"].jwt_secret,ttl_seconds=3600)
    async with AsyncClient(transport=ASGITransport(app=api),base_url="http://test",headers={"Authorization":"Bearer "+token}) as http:
        payload={"idempotency_key":"api","effective_at":INSTANT.isoformat(),"category_id":await finance["pool"].fetchval("SELECT min(id) FROM expense_categories"),"instrument":"USDT","amount":"1000","payer":"pe","shares":{"common":"100"}}
        response=await http.post("/api/v1/expenses",json=payload)
        assert response.status_code==200,response.text
        assert (await http.post("/api/v1/expenses",json=payload)).json()==response.json()
        invalid=await http.post("/api/v1/expenses",json={**payload,"amount":1000.1})
        assert invalid.status_code==422
        assert invalid.json()["error"]["code"]=="invalid_request"
        assert invalid.json()["error"]["fields"]==[{"path":["body","amount"],"type":"string_type"}]
        expense=(await http.get("/api/v1/expenses")).json()["items"][0]
        assert expense["amount"]=="1000.00000000"
        details=(await http.get(f'/api/v1/expenses/{expense["id"]}')).json()
        debts=(await http.get("/api/v1/expense-debts")).json()
        pe_share=next(row for row in debts["expense_shares"] if row["code"]=="pe")
        pe_wallet=next(row for row in debts["wallet_balances"] if row["code"]=="pe")
        allocation=next(row for row in details["allocations"] if row["party_id"]==pe_share["party_id"])
        assert allocation["amount_usdt"]=="500.00000000"
        assert all(isinstance(value,str) for value in
            (pe_share["expense_share_usdt"],pe_share["personally_paid_usdt"],pe_wallet["wallet_balance"]))
        assert Decimal(pe_share["expense_share_usdt"])==500
        assert Decimal(pe_share["personally_paid_usdt"])==1000
        assert Decimal(pe_wallet["wallet_balance"])==1000
        binding=await http.post("/api/v1/expenses/participants/pe/binding",json={"idempotency_key":"bind-api","client_id":clients["pe"]})
        assert binding.status_code==200,binding.text


async def test_expense_wallet_documents_are_immutable_and_notify_affected_chats(finance):
    clients=await bind_wallets(finance)
    for index,code in enumerate(("pe","daniyar")):
        await finance["app"].clients.bind_chat(key="chat-"+code,actor_id=finance["actor"],client_id=clients[code],chat_id=-100-index)
    op=await record(finance,instrument="USDT",rate=None)
    with pytest.raises(asyncpg.CheckViolationError):
        await finance["pool"].execute("UPDATE expense_wallet_entries SET debit_usdt=1 WHERE operation_id=$1",op.id)
    bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=7)),edit_message_text=AsyncMock())
    delivery=ThaiTelegramDelivery(finance["app"],bot,None)
    event=await OutboxDeliveryRepository(finance["pool"]).claim()
    assert event
    await delivery.deliver(event)
    await delivery.deliver(event)
    assert bot.send_message.await_count==2
    messages=[call.args[1] if len(call.args)>1 else call.kwargs['text'] for call in bot.send_message.await_args_list]
    assert any("Компенсация оплаты" in text for text in messages)
    assert any("Ваша доля расхода" in text for text in messages)


async def test_database_guard_rejects_mismatched_wallet_document_atomically(finance,monkeypatch):
    from db_asyncpg.thai_expenses.repository import ExpenseRepository
    original=ExpenseRepository.save_expense
    async def corrupted(repo,operation_id,*args):
        result=await original(repo,operation_id,*args)
        await repo._db.execute("UPDATE expense_wallet_entries SET credit_usdt=credit_usdt+1 WHERE operation_id=$1 AND participant_code='pe'",operation_id)
        return result
    monkeypatch.setattr(ExpenseRepository,"save_expense",corrupted)
    with pytest.raises(InvalidPosting,match="ограничения"):
        await record(finance,instrument="USDT",rate=None)
    assert await totals(finance)=={}
    assert await finance["pool"].fetchval("SELECT count(*) FROM expenses")==0


async def test_large_amount_preserves_wallet_precision(finance):
    amount="100000000000000000000000000000.125000"
    await record(finance,instrument="USDT",rate=None,amount=amount)
    assert (await totals(finance))["pe"]==Decimal(amount)
    assert (await totals(finance))["daniyar"]==Decimal("-20000000000000000000000000000.025000")
    assert await ledger(finance,"operating_expense")==Decimal("80000000000000000000000000000.100000")


async def test_wallet_repayment_after_month_close_marks_snapshot_stale(finance):
    await record(finance,instrument="USDT",rate=None,shares={"common":"100"})
    await fund(finance,instrument="USDT",quantity=Decimal(1000))
    await finance["app"].expenses.close(key="close",actor_id=finance["actor"],period=PERIOD,effective_at=INSTANT)
    before=await finance["app"].expense_queries.statement(PERIOD)
    await pay(finance,amount="100")
    after=await finance["app"].expense_queries.statement(PERIOD)
    assert after["rows"]==before["rows"]
    assert after["needs_revision"]
    assert (await totals(finance))["pe"]==900


async def test_cash_expense_without_wallet_changes_still_notifies_operations(finance):
    await fund(finance,instrument="USDT",quantity=Decimal(1000))
    op=await record(finance,payer="business",instrument="USDT",rate=None,shares={"common":"100"})
    bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=8)),edit_message_text=AsyncMock())
    delivery=ThaiTelegramDelivery(finance["app"],bot,-999)
    await delivery.deliver_expense(op.id)
    await delivery.deliver_expense(op.id)
    bot.send_message.assert_awaited_once()
    text=bot.send_message.call_args.args[1]
    assert "Расход бизнеса: 1000" in text and "Общая касса" in text
    await finance["app"].expenses.reverse(**common(finance,"undo"),operation_id=op.id,reason="Fix")
    await delivery.deliver_expense(op.id)
    assert "Отменён расход" in bot.edit_message_text.call_args.args[0]


async def test_expense_telegram_failure_uses_retryable_transport_error(finance):
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import SendMessage

    from services.finance.outbox import DeliveryError

    op=await record(finance,instrument="USDT",rate=None,shares={"common":"100"})
    bot=SimpleNamespace(send_message=AsyncMock(side_effect=TelegramNetworkError(
        method=SendMessage(chat_id=-999,text="Test"),message="Network unavailable")))
    with pytest.raises(DeliveryError,match="expense_delivery_failed"):
        await ThaiTelegramDelivery(finance["app"],bot,-999).deliver_expense(op.id)
    assert await finance["pool"].fetchval("SELECT count(*) FROM telegram_message_links WHERE operation_id=$1",op.id)==0


async def test_expense_shares_exclude_cancelled_expenses_and_unrelated_wallet_movements(finance):
    wallets = await bind_wallets(finance)
    app = finance["app"]
    await app.wallet_adjustments.adjust(**common(finance, "pe-wallet"), client_id=wallets["pe"],
        instrument="USDT", quantity="-687")
    await record(finance, key="shared", payer="skyex", amount="900", instrument="USDT", rate=None,
        equal_split=True)
    cancelled = await record(finance, key="cancelled", payer="pe", amount="100",
        instrument="USDT", rate=None, shares={"common": "100"})
    await app.expenses.reverse(**common(finance, "undo-cancelled"), operation_id=cancelled.id,
        reason="Duplicate expense")
    balances = await app.expense_queries.debts()
    shares = {row["code"]: row for row in balances["expense_shares"]}
    assert {code: row["expense_share_usdt"] for code, row in shares.items()} == {
        "pe": 300, "skyex": 300, "daniyar": 300,
    }
    assert shares["pe"]["personally_paid_usdt"] == 0
    assert shares["skyex"]["personally_paid_usdt"] == 900
    assert {row["code"]: row["wallet_balance"] for row in balances["wallet_balances"]}["pe"] == -687
    filtered = await app.expense_queries.debts(participant_id=shares["pe"]["party_id"])
    assert filtered["expense_shares"] == [shares["pe"]]
