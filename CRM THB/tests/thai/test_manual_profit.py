from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from domain.finance.models import IdempotencyConflict, InvalidPosting, PeriodClosed, ReversalBlocked
from tests.thai.test_posting import DAY, INSTANT


async def record(finance, key='profit', amount='125.12345678', **options):
    return await finance['app'].manual_profit.record(key=key, actor_id=finance['actor'],
        amount_usdt=amount, effective_at=INSTANT, **options)


async def balances(finance):
    return await finance['pool'].fetch("""
        SELECT a.id,b.balance,b.carrying_usdt,b.unvalued_native FROM ledger_accounts a
        JOIN posted_account_balances b ON b.account_id=a.id
        WHERE a.purpose IN ('cash','client_wallet') ORDER BY a.id
    """)


async def report(finance, period=date(2026, 10, 1)):
    return await finance['app'].profit_queries.report(period)


async def test_profit_and_loss_only_affect_pnl_and_reference_fields(finance):
    cid = (await finance['app'].clients.create_client(key='client', actor_id=finance['actor'], name='Роджер'))['client_id']
    await finance['app'].wallets.deposit(key='cash', actor_id=finance['actor'], client_id=cid,
        instrument='THB_CASH', quantity='1000', business_date=DAY, effective_at=INSTANT)
    before = await balances(finance)
    movement_count = await finance['pool'].fetchval('SELECT count(*) FROM inventory_movements')
    result = await record(finance, client_id=cid, thb_amount='100000.25', comment='Историческая сделка')
    assert await record(finance, client_id=cid, thb_amount='100000.25', comment='Историческая сделка') == result
    with pytest.raises(IdempotencyConflict):
        await record(finance, amount='126', client_id=cid, thb_amount='100000.25', comment='Историческая сделка')
    await record(finance, key='loss', amount='-25.12345678')
    assert await balances(finance) == before
    totals = await report(finance)
    assert totals['income_usdt'] == totals['net_profit_usdt'] == Decimal('100')
    assert totals['business_expenses_usdt'] == totals['investor_interest_usdt'] == 0
    detail = await finance['app'].trading_queries.detail(result['deal_id'])
    assert detail['kind'] == 'manual_profit' and detail['client_id'] == cid
    assert detail['attributes']['thb_amount'] == '100000.25'
    assert detail['attributes']['comment'] == 'Историческая сделка'
    assert await finance['pool'].fetchval('SELECT count(*) FROM deal_legs') == 0
    assert await finance['pool'].fetchval('SELECT count(*) FROM inventory_movements') == movement_count
    assert await finance['pool'].fetchval('SELECT count(*) FROM fx_rate_snapshots') == 0
    from db_asyncpg.thai_trading.workspace import DealWorkspaceQueries
    workspace = await DealWorkspaceQueries(finance['pool']).workspace(kind='profit')
    assert len(workspace['items']) == 2 and workspace['items'][0]['kind'] == 'profit'
    from zoneinfo import ZoneInfo
    assert workspace['items'][0]['created_at'].astimezone(ZoneInfo('Asia/Bangkok')).date() == DAY


async def test_correction_moves_profit_between_months_and_cancel_preserves_history(finance):
    original = await record(finance, business_date=date(2026, 9, 15))
    service = finance['app'].manual_profit
    options = {'key':'edit', 'actor_id':finance['actor'], 'deal_id':original['deal_id'],
        'expected_deal_id':original['deal_id'], 'effective_at':INSTANT, 'business_date':DAY,
        'reason':'Уточнили сумму', 'replacement':{'amount_usdt': '-20', 'comment': 'Убыток'}}
    corrected = await service.change(**options)
    assert await service.change(**options) == corrected
    assert (await report(finance, date(2026, 9, 1)))['net_profit_usdt'] == 0
    assert (await report(finance))['net_profit_usdt'] == -20
    old = await finance['app'].trading_queries.detail(original['deal_id'])
    assert old['effective_status'] == 'cancelled' and old['replacement_deal_id'] == corrected['deal_id']
    with pytest.raises(InvalidPosting, match='изменена'):
        await service.change(**{**options, 'key': 'stale'})
    with pytest.raises(ReversalBlocked):
        await finance['app'].reversals.reverse(key='bypass', actor_id=finance['actor'],
            original_id=corrected['operation_id'], effective_at=INSTANT, business_date=DAY, reason='Нет')
    await service.change(key='cancel', actor_id=finance['actor'], deal_id=corrected['deal_id'], reason='Дубликат')
    assert (await report(finance))['net_profit_usdt'] == 0
    assert await finance['pool'].fetchval('SELECT count(*) FROM deals') == 2
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == 4


async def test_closed_month_and_failed_correction_are_atomic(finance):
    original = await record(finance)
    await finance['pool'].execute("INSERT INTO period_locks(context,period) VALUES ('ledger','2026-10-01')")
    with pytest.raises(PeriodClosed):
        await record(finance, key='blocked')
    with pytest.raises(PeriodClosed):
        await finance['app'].manual_profit.change(key='cancel', actor_id=finance['actor'],
            deal_id=original['deal_id'], reason='Отмена')
    await finance['pool'].execute('DELETE FROM period_locks')
    with pytest.raises(InvalidPosting):
        await finance['app'].manual_profit.change(key='invalid', actor_id=finance['actor'],
            deal_id=original['deal_id'], reason='Неверный клиент',
            replacement={'amount_usdt': '200', 'client_id': 999999})
    assert (await report(finance))['net_profit_usdt'] == Decimal('125.12345678')
    assert await finance['pool'].fetchval('SELECT count(*) FROM deals') == 1
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == 1


@pytest.mark.parametrize('amount,thb', [('0',None),('NaN',None),('Infinity',None),('1.000000001',None),('1','0'),('1','-1'),('1','1.001')])
async def test_invalid_amounts_create_nothing(finance, amount, thb):
    with pytest.raises(InvalidPosting):
        await record(finance, amount=amount, thb_amount=thb)
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == 0
    assert await finance['pool'].fetchval('SELECT count(*) FROM deals') == 0


async def test_api_requires_amount_defaults_today_and_enforces_permissions(finance):
    api = create_thai_finance_api(container=finance['app'], jwt_secret='x'*32)
    token = create_access_token(tg_user_id=12345, secret='x'*32, ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=api), base_url='http://test') as http:
        assert (await http.post('/api/v1/deals/profit', json={'idempotency_key': 'a','amount_usdt':'10'})).status_code == 401
        http.headers['Authorization'] = 'Bearer '+token
        assert (await http.post('/api/v1/deals/profit', json={'idempotency_key': 'missing'})).status_code == 422
        response = await http.post('/api/v1/deals/profit', json={'idempotency_key': 'today','amount_usdt':'-12.50'})
        assert response.status_code == 200, response.text
        assert (await http.post('/api/v1/deals/profit', json={'idempotency_key': 'today','amount_usdt':'-12.50'})).json() == response.json()
        from zoneinfo import ZoneInfo
        assert response.json()['business_date'] == datetime.now(ZoneInfo('Asia/Bangkok')).date().isoformat()
        result = await http.post(f"/api/v1/deals/{response.json()['deal_id']}/profit-correction", json={
            'idempotency_key':'fix','expected_deal_id':response.json()['deal_id'],'reason':'Уточнение',
            'amount_usdt':'15','business_date':'2026-10-08'})
        assert result.status_code == 200, result.text
        cancelled = await http.post(f"/api/v1/deals/{result.json()['deal_id']}/profit-cancel", json={
            'idempotency_key':'cancel','expected_deal_id':result.json()['deal_id'],'reason':'Отмена'})
        assert cancelled.status_code == 200, cancelled.text
        user = await finance['pool'].fetchval("INSERT INTO users(telegram_user_id,display_name) VALUES (777,'Cashier') RETURNING id")
        await finance['pool'].execute("INSERT INTO user_roles VALUES ($1,'cashier')", user)
        http.headers['Authorization'] = 'Bearer '+create_access_token(tg_user_id=777, secret='x'*32, ttl_seconds=60)
        assert (await http.post('/api/v1/deals/profit', json={'idempotency_key':'denied','amount_usdt':'1'})).status_code == 403


async def test_closure_distributes_recorded_net_profit_once(finance):
    await record(finance, amount='100')
    await record(finance, key='loss', amount='-20')
    before = await balances(finance)
    result = await finance['app'].profit.close(key='close', actor_id=finance['actor'],
        period=date(2026,10,1), effective_at=datetime(2026,11,2,tzinfo=UTC))
    assert result.id
    closure = await finance['app'].profit_queries.closure(date(2026,10,1))
    assert closure['net_profit_usdt'] == 80
    assert sum(row['amount_usdt'] for row in closure['accruals']) == 80
    assert (await report(finance))['net_profit_usdt'] == 80
    assert await balances(finance) == before


async def test_financial_history_is_immutable_and_does_not_notify_clients(finance):
    import asyncpg

    from app.thai_telegram import ThaiTelegramDelivery
    from tests.thai.telegram_stub import telegram_mock
    original = await record(finance)
    with pytest.raises(asyncpg.CheckViolationError):
        await finance['pool'].execute("UPDATE deals SET attributes='{}'::jsonb WHERE id=$1", original['deal_id'])
    with pytest.raises(asyncpg.CheckViolationError):
        await finance['pool'].execute("UPDATE journal_lines SET amount_usdt=1 WHERE operation_id=$1", original['operation_id'])
    bot = telegram_mock()
    worker = finance['app'].outbox_dispatcher(ThaiTelegramDelivery(finance['app'], bot, -999))
    while await worker.run_once():
        pass
    await finance['app'].manual_profit.change(key='cancel', actor_id=finance['actor'], deal_id=original['deal_id'], reason='Отмена')
    while await worker.run_once():
        pass
    bot.send_message.assert_not_awaited()
    bot.edit_message_text.assert_not_awaited()


async def test_negative_month_has_no_owner_distribution(finance):
    await record(finance, amount='-20')
    await finance['app'].profit.close(key='close', actor_id=finance['actor'],
        period=date(2026,10,1), effective_at=datetime(2026,11,2,tzinfo=UTC))
    closure = await finance['app'].profit_queries.closure(date(2026,10,1))
    assert closure['net_profit_usdt'] == -20
    assert closure['accruals'] == []
    assert (await report(finance))['net_profit_usdt'] == -20
