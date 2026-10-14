from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from domain.finance.models import (
    FxQuote,
    IdempotencyConflict,
    InsufficientCash,
    InvalidPosting,
    JournalLine,
    OperationCommand,
    PeriodClosed,
    PermissionDenied,
    PostingMode,
    PostingPlan,
    ReversalBlocked,
    Side,
    Valuation,
    canonical_json,
)
from services.finance.outbox import DeliveryError

DAY = date(2026, 10, 8)
INSTANT = datetime(2026, 10, 8, 12, tzinfo=UTC)




async def fund(finance, *, key="fund", quantity=Decimal(35000), instrument="THB_CASH",
               document=None):
    account = finance["cash"][instrument]
    quote = FxQuote("USDT", Decimal(1), instrument="USDT") if instrument == "USDT" else FxQuote(
        "THB", Decimal(35), "units_per_usdt", instrument)
    cost = quote.value(quantity)
    contra = finance["equity"]
    command = OperationCommand(key, "manual_journal", finance["actor"], DAY, INSTANT,
        tuple(sorted((account, contra))), canonical_json({"quantity": quantity, "instrument": instrument}))
    def build(state):
        return PostingPlan((
            JournalLine(account, quote.currency, Side.DEBIT, quantity, cost, Valuation.RATE, quote),
            JournalLine(contra, "USDT", Side.CREDIT, cost, cost, Valuation.BASE),
        ))
    return await finance["app"].posting.post(command, build, document=document)


async def transfer(finance, *, key="transfer", quantity=Decimal(3500)):
    return await finance["app"].cash_transfers.transfer(key=key, actor_id=finance["actor"],
        business_date=DAY, effective_at=INSTANT, source_id=finance["cash"]["THB_CASH"],
        target_id=finance["cash"]["THB_BANK"], quantity=quantity)


async def balance(finance, instrument):
    row = await finance["pool"].fetchrow("""
        SELECT b.balance,b.carrying_usdt FROM cash_accounts c
        JOIN posted_account_balances b ON b.account_id=c.ledger_account_id WHERE c.instrument_code=$1
    """, instrument)
    return tuple(row)


async def test_posting_updates_cash_cost_projection_and_outbox_atomically(finance):
    original = await fund(finance)
    await transfer(finance)
    assert await balance(finance, "THB_CASH") == (Decimal(31500), Decimal(900))
    assert await balance(finance, "THB_BANK") == (Decimal(3500), Decimal(100))
    assert await finance["pool"].fetchval("SELECT count(*) FROM telegram_outbox") == 2
    assert await finance["pool"].fetchval("SELECT count(*) FROM inventory_movements") == 3
    assert await finance["pool"].fetchval("SELECT count(*) FROM financial_operations WHERE status='draft'") == 0
    assert await finance["pool"].fetchval("SELECT quoted_rate FROM fx_rate_snapshots") == 35
    assert original.id > 0


async def test_repeat_and_parallel_same_key_have_exactly_one_posting(finance):
    results = await asyncio.gather(*(fund(finance) for _ in range(5)))
    assert len({result.id for result in results}) == 1
    assert await balance(finance, "THB_CASH") == (Decimal(35000), Decimal(1000))
    assert await finance["pool"].fetchval("SELECT count(*) FROM telegram_outbox") == 1
    with pytest.raises(IdempotencyConflict):
        await fund(finance, quantity=Decimal(70000))


async def test_concurrent_withdrawals_cannot_overspend(finance):
    await fund(finance)
    results = await asyncio.gather(transfer(finance, key="a", quantity=Decimal(21000)),
                                   transfer(finance, key="b", quantity=Decimal(21000)), return_exceptions=True)
    assert sum(isinstance(result, InsufficientCash) for result in results) == 1
    assert await balance(finance, "THB_CASH") == (Decimal(14000), Decimal(400))
    assert await balance(finance, "THB_BANK") == (Decimal(21000), Decimal(600))


async def test_failure_after_document_before_projection_rolls_back_everything(finance):
    class BrokenDocument:
        async def save(self, operation_id, uow, plan):
            await uow.documents.save_note(operation_id, "Inserted before failure")
            raise RuntimeError("simulated document failure")
    with pytest.raises(RuntimeError, match="simulated"):
        await fund(finance, document=BrokenDocument())
    for table in ("financial_operations", "journal_lines", "operation_audit_notes", "telegram_outbox", "fx_rate_snapshots"):
        assert await finance["pool"].fetchval(f"SELECT count(*) FROM {table}") == 0
    assert await balance(finance, "THB_CASH") == (0, 0)


async def test_failure_after_journal_and_projection_before_commit_rolls_back(finance, monkeypatch):
    from db_asyncpg.thai_finance.repositories import OutboxRepository
    async def broken_enqueue(self, operation):
        raise RuntimeError("simulated outbox insert failure")
    monkeypatch.setattr(OutboxRepository, "enqueue", broken_enqueue)
    with pytest.raises(RuntimeError, match="simulated"):
        await fund(finance)
    assert await balance(finance, "THB_CASH") == (0, 0)
    assert await finance["pool"].fetchval("SELECT sum(quantity) FROM inventory_balances") == 0
    assert await finance["pool"].fetchval("SELECT count(*) FROM inventory_movements") == 0
    assert await finance["pool"].fetchval("SELECT count(*) FROM financial_operations") == 0


async def test_storno_uses_exact_original_cost_and_is_idempotent(finance):
    await fund(finance)
    moved = await transfer(finance)
    kwargs = {"original_id": moved.id, "key": "reverse", "actor_id": finance["actor"],
              "business_date": DAY, "effective_at": INSTANT, "reason": "Correction"}
    result = await finance["app"].reversals.reverse(**kwargs)
    assert await finance["app"].reversals.reverse(**kwargs) == result
    assert await balance(finance, "THB_CASH") == (35000, 1000)
    assert await balance(finance, "THB_BANK") == (0, 0)
    with pytest.raises(ReversalBlocked):
        await finance["app"].reversals.reverse(**{**kwargs, "key": "second-reverse"})


async def test_cannot_reverse_before_later_cash_movement(finance):
    original = await fund(finance)
    await transfer(finance)
    with pytest.raises(ReversalBlocked):
        await finance["app"].reversals.reverse(original_id=original.id, key="reverse-fund",
            actor_id=finance["actor"], business_date=DAY, effective_at=INSTANT, reason="Correction")


async def test_projection_rebuild_restores_both_native_and_cost(finance):
    await fund(finance)
    await transfer(finance)
    await finance["pool"].execute("UPDATE account_balances SET debit_signed_native=0,debit_signed_usdt=0")
    await finance["pool"].execute("UPDATE inventory_balances SET quantity=0,cost_usdt=0")
    await finance["app"].projections.rebuild(finance["actor"])
    projection = await finance["pool"].fetchrow("SELECT quantity,cost_usdt FROM inventory_balances WHERE instrument_code='THB_BANK'")
    assert tuple(projection) == (3500, 100)
    signed = await finance["pool"].fetchrow("SELECT debit_signed_native,debit_signed_usdt FROM account_balances WHERE account_id=$1", finance["equity"])
    assert tuple(signed) == (-1000, -1000)


async def test_closed_period_rejects_new_posting_but_allows_identical_retry(finance):
    original = await fund(finance)
    await finance["pool"].execute("INSERT INTO period_locks(context,period) VALUES ('ledger','2026-10-01')")
    assert await fund(finance) == original
    with pytest.raises(PeriodClosed):
        await transfer(finance)
    with pytest.raises(PeriodClosed):
        await finance["app"].reversals.reverse(original_id=original.id, key="closed-reverse",
            actor_id=finance["actor"], business_date=date(2026, 11, 1), effective_at=INSTANT, reason="Correction")


async def test_permissions_are_checked_in_service_not_only_api(finance):
    await finance["pool"].execute("DELETE FROM user_roles WHERE user_id=$1", finance["actor"])
    with pytest.raises(PermissionDenied):
        await fund(finance)


async def test_bad_wac_and_precision_leave_no_operation(finance):
    await fund(finance)
    with pytest.raises(InvalidPosting):
        await transfer(finance, quantity=Decimal("1.001"))
    assert await finance["pool"].fetchval("SELECT count(*) FROM financial_operations") == 1


async def test_delivery_failure_does_not_undo_or_repeat_financial_posting(finance):
    operation = await fund(finance)
    class BrokenTransport:
        async def deliver(self, event):
            raise DeliveryError("transport token should never be stored")
    assert await finance["app"].outbox_dispatcher(BrokenTransport()).run_once()
    assert await finance["pool"].fetchval("SELECT status FROM telegram_outbox") == "failed"
    assert await finance["pool"].fetchval("SELECT last_error FROM telegram_outbox") == "delivery_failed"
    assert await fund(finance) == operation
    assert await balance(finance, "THB_CASH") == (35000, 1000)
    assert await finance["pool"].fetchval("SELECT count(*) FROM financial_operations") == 1


async def test_finance_api_uses_decimal_strings_permissions_and_audit(finance):
    await fund(finance)
    secret = "x" * 32
    api = create_thai_finance_api(container=finance["app"], jwt_secret=secret)
    token = create_access_token(tg_user_id=12345, secret=secret, ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        assert (await client.get('/api/v1/finance/cash')).status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        cash = await client.get('/api/v1/finance/cash', headers=headers)
        assert cash.status_code == 200
        row = next(row for row in cash.json() if row['instrument_code']=='THB_CASH')
        assert row['balance'] == '35000.00000000'
        payload = {"idempotency_key": "api-transfer", "source_account_id": finance["cash"]["THB_CASH"],
            "target_account_id": finance["cash"]["THB_BANK"], "quantity": "3500", "effective_at": INSTANT.isoformat()}
        transfer_response = await client.post('/api/v1/finance/cash-transfers', json=payload, headers=headers)
        assert transfer_response.status_code == 200
        repeated = await client.post('/api/v1/finance/cash-transfers', json=payload, headers=headers)
        assert repeated.json()['id'] == transfer_response.json()['id']
        history = (await client.get('/api/v1/finance/operations?limit=1', headers=headers)).json()
        assert len(history['items']) == 1
        assert history['next_before_id'] is not None
        next_page = (await client.get(f"/api/v1/finance/operations?before_id={history['next_before_id']}", headers=headers)).json()
        assert len(next_page['items']) == 1
        detail = await client.get(f"/api/v1/finance/operations/{transfer_response.json()['id']}", headers=headers)
        assert isinstance(detail.json()['lines'][0]['amount_usdt'], str)
        assert detail.json()['created_by'] == finance['actor']
        assert isinstance(detail.json()['command_details'], dict)
        invalid = await client.post('/api/v1/finance/cash-transfers', json={**payload, "quantity": 1.5}, headers=headers)
        assert invalid.status_code == 422
        assert invalid.json()['error']['code'] == 'invalid_request'


async def test_accrual_can_make_client_balance_negative_without_cash_movement(finance):
    wallet = await finance['pool'].fetchval("""
        INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code,instrument_code)
        VALUES ('test-wallet','liability','client_wallet','credit','USDT','USDT') RETURNING id
    """)
    contra = finance['equity']
    command = OperationCommand('accrual', 'manual_journal', finance['actor'], DAY, INSTANT,
        tuple(sorted((wallet, contra))), '{}')
    await finance['app'].posting.post(command, lambda state: PostingPlan((
        JournalLine(wallet, 'USDT', Side.DEBIT, Decimal(10), Decimal(10), Valuation.BASE),
        JournalLine(contra, 'USDT', Side.CREDIT, Decimal(10), Decimal(10), Valuation.BASE))))
    assert await finance['pool'].fetchval('SELECT balance FROM posted_account_balances WHERE account_id=$1', wallet) == -10
    assert await finance['pool'].fetchval('SELECT count(*) FROM inventory_movements') == 0
    assert all(row['balance'] == 0 for row in await finance['app'].queries.cash_balances())


async def test_memo_document_does_not_create_financial_balances(finance):
    class Note:
        async def save(self, operation_id, uow, plan):
            await uow.documents.save_note(operation_id, 'Manager decision')
    command = OperationCommand('decision', 'manual_journal', finance['actor'], DAY, INSTANT,
        (), '{}', mode=PostingMode.MEMORANDUM)
    result = await finance['app'].posting.post(command, lambda state: PostingPlan((), PostingMode.MEMORANDUM), document=Note())
    assert (await finance['app'].queries.detail(result.id))['note'] == 'Manager decision'
    assert await finance['pool'].fetchval('SELECT count(*) FROM journal_lines') == 0
    assert await finance['pool'].fetchval('SELECT count(*) FROM inventory_movements') == 0


async def test_wrong_cost_cannot_be_used_to_withdraw_cash(finance):
    await fund(finance)
    source, target = finance['cash']['THB_CASH'], finance['cash']['THB_BANK']
    command = OperationCommand('wrong-cost', 'cash_transfer', finance['actor'], DAY, INSTANT,
        tuple(sorted((source, target))), '{}')
    with pytest.raises(InvalidPosting, match='себестоимость'):
        await finance['app'].posting.post(command, lambda state: PostingPlan((
            JournalLine(source, 'THB', Side.CREDIT, Decimal(3500), Decimal(50), Valuation.CARRYING),
            JournalLine(target, 'THB', Side.DEBIT, Decimal(3500), Decimal(50), Valuation.CARRYING))))
    assert await balance(finance, 'THB_CASH') == (35000, 1000)
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == 1


async def test_full_cash_transfer_keeps_all_digits_and_clears_source_cost(finance):
    quantity = Decimal('12345678901234567890123456789.12')
    await fund(finance, quantity=quantity)
    original_balance = await balance(finance, 'THB_CASH')
    await transfer(finance, quantity=quantity)
    assert await balance(finance, 'THB_CASH') == (0, 0)
    assert await balance(finance, 'THB_BANK') == original_balance


async def test_expired_outbox_lease_can_be_reclaimed_but_old_worker_cannot_ack(finance):
    from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository
    await fund(finance)
    repository = OutboxDeliveryRepository(finance['pool'])
    first = await repository.claim()
    assert first is not None
    assert await repository.claim() is None
    await finance['pool'].execute("UPDATE telegram_outbox SET lease_until=clock_timestamp()-interval '1 second'")
    second = await repository.claim()
    assert second is not None and second.lease_token != first.lease_token
    await repository.delivered(first)
    assert await finance['pool'].fetchval('SELECT status FROM telegram_outbox') == 'processing'
    await repository.delivered(second)
    assert await finance['pool'].fetchval('SELECT status FROM telegram_outbox') == 'sent'


async def test_reversal_requires_dependent_operation_to_be_reversed_first(finance):
    original = await fund(finance)
    class DependentDocument:
        async def save(self, operation_id, uow, plan):
            await uow.journal.add_dependency(original.id, operation_id)
    command = OperationCommand('dependent', 'manual_journal', finance['actor'], DAY, INSTANT,
        (), '{}', mode=PostingMode.MEMORANDUM)
    dependent = await finance['app'].posting.post(command,
        lambda state: PostingPlan((), PostingMode.MEMORANDUM), document=DependentDocument())
    kwargs = {'actor_id': finance['actor'], 'business_date': DAY, 'effective_at': INSTANT, 'reason': 'Correction'}
    with pytest.raises(ReversalBlocked, match='зависимые'):
        await finance['app'].reversals.reverse(original_id=original.id, key='blocked', **kwargs)
    await finance['app'].reversals.reverse(original_id=dependent.id, key='reverse-dependent', **kwargs)
    await finance['app'].reversals.reverse(original_id=original.id, key='reverse-original', **kwargs)
    assert await balance(finance, 'THB_CASH') == (0, 0)
