import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from domain.finance.models import (
    IdempotencyConflict,
    InvalidPosting,
    PeriodClosed,
    PermissionDenied,
    ReversalBlocked,
)
from tests.thai.test_posting import balance

DAY = date(2026, 9, 2)
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def investor(finance):
    result = await finance['app'].capital.create_agreement(key='new-investor', actor_id=finance['actor'],
        business_date=date(2026, 9, 1), effective_at=NOW, monthly_interest_percent=Decimal(2), name='Investor')
    agreement = await finance['pool'].fetchval('SELECT id FROM capital_agreements WHERE created_operation_id=$1', result.id)
    return agreement


async def contribute(finance, investor, *, key='contribution', instrument='USDT', quantity='10000', rate=None, day=DAY):
    return await finance['app'].capital.contribute(key=key, actor_id=finance['actor'], agreement_id=investor,
        instrument=instrument, quantity=quantity, quoted_rate=rate, business_date=day, effective_at=NOW)


async def withdraw(finance, investor, *, key='withdrawal', instrument='USDT', quantity='1000', day=DAY):
    return await finance['app'].capital.withdraw(key=key, actor_id=finance['actor'], agreement_id=investor,
        instrument=instrument, quantity=quantity, business_date=day, effective_at=NOW)


async def accrue(finance, investor, *, key='accrual', period=date(2026, 10, 1), approved=None):
    from calendar import monthrange
    return await finance['app'].capital.accrue(key=key, actor_id=finance['actor'], agreement_id=investor,
        period=period, business_date=date(period.year, period.month, monthrange(period.year, period.month)[1]),
        effective_at=datetime(2026, 12, 2, tzinfo=UTC), approved_usdt=approved, reason='Manager approved')


async def pay(finance, investor, *, key='payment', quantity='150', instrument='USDT', rate=None):
    return await finance['app'].capital.pay_interest(key=key, actor_id=finance['actor'], agreement_id=investor,
        instrument=instrument, quantity=quantity, quoted_rate=rate, business_date=date(2026, 12, 2),
        effective_at=datetime(2026, 12, 2, tzinfo=UTC), reason='Manager payout decision')


async def reverse(finance, investor, operation, key='reverse'):
    return await finance['app'].capital.reverse(agreement_id=investor, original_id=operation.id, key=key,
        actor_id=finance['actor'], business_date=DAY, effective_at=NOW, reason='Correction')


async def test_investor_has_no_chat_and_percent_is_versioned(finance, investor):
    agreement = await finance['app'].capital_queries.detail(investor)
    assert agreement['name'] == 'Investor'
    assert agreement['terms'][0]['monthly_interest_percent'] == 2
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients') == 0
    result = await finance['app'].capital.change_term(key='rate', actor_id=finance['actor'], agreement_id=investor,
        valid_from=date(2026, 11, 1), effective_at=NOW, monthly_interest_percent=Decimal(3), reason='New terms')
    assert result.id > 0
    terms = (await finance['app'].capital_queries.detail(investor))['terms']
    assert [t['monthly_interest_percent'] for t in terms] == [2, 3]
    assert terms[0]['valid_until'] == date(2026, 11, 1)
    owner = await finance['pool'].fetchval("SELECT id FROM capital_agreements WHERE principal_kind='owner_equity' LIMIT 1")
    with pytest.raises(InvalidPosting):
        await finance['app'].capital.change_term(key='owner-rate', actor_id=finance['actor'], agreement_id=owner,
            valid_from=date(2026, 11, 1), effective_at=NOW, monthly_interest_percent=Decimal(2), reason='No')


async def test_usd_contribution_is_capital_and_never_income(finance, investor):
    operation = await contribute(finance, investor, instrument='USD_BL', quantity='10000', rate='1')
    assert await balance(finance, 'USD_BL') == (10000, 10000)
    agreement = await finance['app'].capital_queries.detail(investor)
    assert agreement['principal_usdt'] == 10000
    assert agreement['lots'][0]['rate_snapshot_id'] is not None
    assert await finance['pool'].fetchval("SELECT coalesce(sum(b.carrying_usdt),0) FROM ledger_accounts a JOIN posted_account_balances b ON b.account_id=a.id WHERE a.account_class='income'") == 0
    assert await contribute(finance, investor, instrument='USD_BL', quantity='10000', rate='1') == operation
    with pytest.raises(IdempotencyConflict):
        await contribute(finance, investor, instrument='USD_BL', quantity='10001', rate='1')
    with pytest.raises(InvalidPosting):
        await withdraw(finance, investor, instrument='USD_WH', quantity='100')


async def test_two_thb_lots_fifo_cost_and_exact_storno(finance, investor):
    await contribute(finance, investor, key='one', instrument='THB_CASH', quantity='35000', rate='35')
    await contribute(finance, investor, key='two', instrument='THB_CASH', quantity='18000', rate='36', day=date(2026, 9, 3))
    operation = await withdraw(finance, investor, instrument='THB_CASH', quantity='40000', day=date(2026, 9, 4))
    agreement = await finance['app'].capital_queries.detail(investor)
    assert agreement['principal_usdt'] == Decimal('361.11111111')
    assert [lot['remaining_quantity'] for lot in agreement['lots']] == [0, 13000]
    assert await balance(finance, 'THB_CASH') == (13000, Decimal('367.92452830'))
    fx = await finance['pool'].fetchval("SELECT balance FROM posted_account_balances WHERE account_id=(SELECT id FROM ledger_accounts WHERE purpose='fx_income')")
    assert fx == Decimal('6.81341719')
    result = await reverse(finance, investor, operation)
    assert await reverse(finance, investor, operation) == result
    assert await balance(finance, 'THB_CASH') == (53000, 1500)
    assert (await finance['app'].capital_queries.detail(investor))['principal_usdt'] == 1500


async def test_parallel_fifo_withdrawals_cannot_overallocate(finance, investor):
    await contribute(finance, investor)
    results = await asyncio.gather(withdraw(finance, investor, key='a', quantity='7000'),
                                   withdraw(finance, investor, key='b', quantity='7000'), return_exceptions=True)
    assert sum(isinstance(r, InvalidPosting) for r in results) == 1
    assert (await finance['app'].capital_queries.detail(investor))['principal_usdt'] == 3000
    assert await balance(finance, 'USDT') == (3000, 3000)


async def test_monthly_interest_and_partial_payment_do_not_touch_capital(finance, investor):
    await contribute(finance, investor)
    result = await accrue(finance, investor)
    assert await accrue(finance, investor) == result
    detail = await finance['app'].capital_queries.detail(investor)
    assert detail['accruals'][0]['basis_usdt'] == 10000
    assert detail['accruals'][0]['monthly_interest_percent'] == 2
    assert detail['accrued_usdt'] == 200
    payment = await pay(finance, investor)
    assert await pay(finance, investor) == payment
    detail = await finance['app'].capital_queries.detail(investor)
    assert (detail['principal_usdt'], detail['paid_usdt'], detail['interest_due_usdt']) == (10000, 150, 50)
    expense = await finance['pool'].fetchval("SELECT balance FROM posted_account_balances WHERE account_id=(SELECT id FROM ledger_accounts WHERE purpose='investor_interest_expense')")
    assert expense == 200
    assert await balance(finance, 'USDT') == (9850, 9850)
    with pytest.raises(InvalidPosting):
        await pay(finance, investor, key='overpay', quantity='51')
    with pytest.raises(ReversalBlocked):
        await reverse(finance, investor, result)
    await reverse(finance, investor, payment)
    assert (await finance['app'].capital_queries.detail(investor))['interest_due_usdt'] == 200
    await reverse(finance, investor, result, key='reverse-accrual')
    assert (await finance['app'].capital_queries.detail(investor))['accrued_usdt'] == 0


async def test_manager_decision_required_when_capital_changes_and_zero_is_valid(finance, investor):
    await contribute(finance, investor, day=date(2026, 10, 10))
    with pytest.raises(InvalidPosting, match='явно'):
        await accrue(finance, investor)
    operation = await accrue(finance, investor, approved='0')
    detail = await finance['app'].capital_queries.detail(investor)
    assert detail['accruals'][0]['final_usdt'] == 0
    assert await finance['pool'].fetchval('SELECT count(*) FROM journal_lines WHERE operation_id=$1', operation.id) == 0
    with pytest.raises(InvalidPosting):
        await contribute(finance, investor, key='late', day=date(2026, 10, 11))


async def test_rate_change_preserves_old_accrual_and_payment_can_cover_months(finance, investor):
    await contribute(finance, investor)
    await accrue(finance, investor)
    await finance['app'].capital.change_term(key='new-rate', actor_id=finance['actor'], agreement_id=investor,
        valid_from=date(2026, 11, 1), effective_at=NOW, monthly_interest_percent=Decimal(3), reason='Agreement')
    await accrue(finance, investor, key='november', period=date(2026, 11, 1), approved='300')
    await pay(finance, investor, quantity='250')
    detail = await finance['app'].capital_queries.detail(investor)
    assert [(a['monthly_interest_percent'], a['paid_usdt'], a['remaining_usdt']) for a in detail['accruals']] == [(2, 200, 0), (3, 50, 250)]
    with pytest.raises(InvalidPosting):
        await finance['app'].capital.change_term(key='retro-rate', actor_id=finance['actor'], agreement_id=investor,
            valid_from=date(2026, 11, 15), effective_at=NOW, monthly_interest_percent=Decimal(4), reason='Invalid')


async def test_capital_api_history_decimal_and_idempotency(finance, investor):
    secret = 'x'*32
    app = create_thai_finance_api(container=finance['app'], jwt_secret=secret)
    token = create_access_token(tg_user_id=12345, secret=secret, ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        headers = {'Authorization': f'Bearer {token}'}
        prefix = f'/api/v1/capital/agreements/{investor}'
        payload = {'idempotency_key': 'api-capital', 'effective_at': NOW.isoformat(), 'business_date': DAY.isoformat(),
            'instrument': 'USDT', 'quantity': '10000'}
        first = await client.post(prefix+'/contributions', json=payload, headers=headers)
        assert first.status_code == 200
        assert (await client.post(prefix+'/contributions', json=payload, headers=headers)).json() == first.json()
        detail = (await client.get(prefix, headers=headers)).json()
        assert detail['principal_usdt'] == '10000.00000000'
        assert detail['terms'][0]['monthly_interest_percent'] == '2.000000'
        history = await client.get(prefix+'/history?limit=1', headers=headers)
        assert history.status_code == 200
        assert history.json()['next_before_id'] is not None
        assert history.json()['items'][0]['command_details']['instrument'] == 'USDT'
        party_id = detail['investor_party_id']
        investors = (await client.get('/api/v1/capital/investors', headers=headers)).json()
        investor_total = next(item for item in investors if item['id'] == party_id)
        assert investor_total['principal_usdt'] == '10000.00000000'
        investor_history = (await client.get(f'/api/v1/capital/investors/{party_id}/history', headers=headers)).json()
        assert len(investor_history['items']) == 2
        filtered = (await client.get(f'/api/v1/capital/agreements?investor_party_id={party_id}', headers=headers)).json()
        assert [a['id'] for a in filtered] == [investor]


async def test_thb_return_can_use_bank_without_changing_lot_currency(finance, investor):
    await contribute(finance, investor, instrument='THB_CASH', quantity='35000', rate='35')
    await finance['app'].cash_transfers.transfer(key='to-bank', actor_id=finance['actor'], business_date=DAY,
        effective_at=NOW, source_id=finance['cash']['THB_CASH'], target_id=finance['cash']['THB_BANK'], quantity=Decimal(3500))
    await withdraw(finance, investor, instrument='THB_BANK', quantity='3500')
    assert await balance(finance, 'THB_BANK') == (0, 0)
    assert (await finance['app'].capital_queries.detail(investor))['principal_usdt'] == 900


async def test_foreign_currency_interest_payment_retains_quote_and_fx(finance, investor):
    await contribute(finance, investor)
    await accrue(finance, investor)
    await contribute(finance, investor, key='thb-capital', instrument='THB_CASH', quantity='3600', rate='36', day=date(2026, 12, 1))
    payment = await pay(finance, investor, instrument='THB_CASH', quantity='3600', rate='35')
    assert await balance(finance, 'THB_CASH') == (0, 0)
    detail = await finance['app'].capital_queries.detail(investor)
    assert detail['principal_usdt'] == 10100
    assert detail['interest_due_usdt'] == Decimal('97.14285714')
    assert await finance['pool'].fetchval("""
        SELECT r.quoted_rate FROM distribution_payments p JOIN fx_rate_snapshots r ON r.id=p.rate_snapshot_id WHERE p.operation_id=$1
    """, payment.id) == 35


async def test_parallel_interest_payments_and_closed_month(finance, investor):
    original = await contribute(finance, investor)
    await accrue(finance, investor)
    results = await asyncio.gather(pay(finance, investor, key='payment-a'), pay(finance, investor, key='payment-b'), return_exceptions=True)
    assert sum(isinstance(r, InvalidPosting) for r in results) == 1
    assert (await finance['app'].capital_queries.detail(investor))['interest_due_usdt'] == 50
    await finance['pool'].execute("INSERT INTO period_locks(context,period) VALUES ('ledger','2026-09-01')")
    assert await contribute(finance, investor) == original
    with pytest.raises(PeriodClosed):
        await withdraw(finance, investor)


async def test_capital_document_failure_rolls_back_journal_and_cash(finance, investor, monkeypatch):
    from db_asyncpg.thai_capital.repository import CapitalRepository
    before = await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')
    original_save = CapitalRepository.contribution
    async def fail_after_document(self, *args):
        await original_save(self, *args)
        raise RuntimeError('Capital document failure')
    monkeypatch.setattr(CapitalRepository, 'contribution', fail_after_document)
    with pytest.raises(RuntimeError, match='Capital document'):
        await contribute(finance, investor)
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == before
    assert await finance['pool'].fetchval('SELECT count(*) FROM capital_contributions') == 0
    assert await balance(finance, 'USDT') == (0, 0)


async def test_manager_cannot_change_investor_terms(finance, investor):
    await finance['pool'].execute("DELETE FROM user_roles WHERE user_id=$1 AND role='owner'", finance['actor'])
    with pytest.raises(PermissionDenied):
        await finance['app'].capital.change_term(key='forbidden', actor_id=finance['actor'], agreement_id=investor,
            valid_from=date(2026, 11, 1), effective_at=NOW, monthly_interest_percent=Decimal(3), reason='No rights')
    # A manager is still allowed to accept a contribution under existing terms.
    await contribute(finance, investor)


async def test_contribution_storno_restores_cash_and_capital_without_deleting_lot(finance, investor):
    contribution = await contribute(finance, investor)
    with pytest.raises(ReversalBlocked):
        await finance['app'].reversals.reverse(original_id=contribution.id, key='generic-reverse',
            actor_id=finance['actor'], business_date=DAY, effective_at=NOW, reason='Wrong service')
    await reverse(finance, investor, contribution)
    assert await balance(finance, 'USDT') == (0, 0)
    detail = await finance['app'].capital_queries.detail(investor)
    assert detail['principal_usdt'] == 0
    assert detail['lots'] == []
    assert detail['lot_history'][0]['active'] is False
    assert detail['lot_history'][0]['quoted_rate'] == 1
    assert detail['lot_history'][0]['quoted_direction'] == 'usdt_per_unit'
    assert await finance['pool'].fetchval('SELECT count(*) FROM capital_contributions') == 1
    history = await finance['app'].capital_queries.history(investor)
    original = next(row for row in history if row['id'] == contribution.id)
    assert original['active'] is False
