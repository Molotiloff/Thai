"""Counterparty commissions must share the exchange journal and its replay history."""
from decimal import Decimal

import pytest

from domain.finance.models import InvalidPosting
from tests.thai.test_exchange_history import (
    assert_consistent,
    client,
    common,
    total_profit,
    wallet_state,
)
from tests.thai.test_posting import fund


async def setup_clients(finance):
    buyer, kt = await client(finance, 'buyer'), await client(finance, 'KT chat')
    await finance['app'].clients.bind_chat(key='kt-chat', actor_id=finance['actor'], client_id=kt, chat_id=-5522)
    return buyer, kt


def legs(purchase=False):
    thb = {'instrument': 'THB_CASH', 'quantity': '33100'}
    usdt = {'instrument': 'USDT', 'quantity': '1000'}
    return (usdt, thb) if purchase else (thb, usdt)


async def create(finance, buyer, kt, *, purchase=False, spread='0.15', key='exchange'):
    receive, pay = legs(purchase)
    return await finance['app'].trading.create_and_post(**common(finance, key), client_id=buyer,
        receive=receive, pay=pay, presentation={}, counterparty_client_id=kt, counterparty_spread=spread)


async def kt_balance(finance, kt):
    return await finance['pool'].fetchval('''SELECT b.balance FROM client_wallet_accounts w
        JOIN posted_account_balances b ON b.account_id=w.ledger_account_id
        WHERE w.client_id=$1 AND w.instrument_code='USDT' AND w.kind='client' ''', kt)


@pytest.mark.parametrize('purchase', [False, True])
async def test_commission_example_profit_retry_and_reversal(finance, purchase):
    buyer, kt = await setup_clients(finance)
    deal = await create(finance, buyer, kt, purchase=purchase)
    assert await create(finance, buyer, kt, purchase=purchase) == deal
    detail = await finance['app'].trading_queries.detail(deal)
    attrs = detail['attributes']
    assert attrs['counterparty']['amount_usdt'] == '4.531722'
    assert Decimal(attrs['counterparty']['company_rate']) == Decimal('32.95' if purchase else '33.25')
    assert await kt_balance(finance, kt) == Decimal('4.531722')
    assert await total_profit(finance) == Decimal('-4.531722')
    assert Decimal(attrs['result_usdt']) == Decimal('-4.531722')
    await assert_consistent(finance)
    await finance['app'].trading.reverse(**common(finance, 'undo'), deal_id=deal, reason='test')
    assert await kt_balance(finance, kt) == 0
    assert await total_profit(finance) == 0
    await assert_consistent(finance)


async def test_cash_exchange_posts_commission_in_same_operation(finance):
    _, kt = await setup_clients(finance)
    await fund(finance, instrument='USDT', quantity=Decimal('1000'))
    receive, pay = legs()
    result = await finance['app'].trading.create(key='cash', actor_id=finance['actor'], client_id=None,
        mode='cash', receive=receive, pay=pay, counterparty_client_id=kt, counterparty_spread='0.15')
    await finance['app'].trading.ready(key='ready', actor_id=finance['actor'], deal_id=result['deal_id'])
    await finance['app'].trading.post(**common(finance, 'post'), deal_id=result['deal_id'])
    assert await kt_balance(finance, kt) == Decimal('4.531722')
    assert await total_profit(finance) == Decimal('-4.531722')
    await assert_consistent(finance)


async def test_correction_replays_counterparty_spend_and_keeps_commission_contract(finance, reference_finance):
    async def scenario(db, spread):
        buyer, kt = await setup_clients(db)
        recipient = await client(db, 'recipient')
        root = await create(db, buyer, kt, spread=spread)
        await db['app'].wallet_transfers.transfer(**common(db, 'spend'), source_client_id=kt,
            target_client_id=recipient, instrument='USDT', quantity='2')
        tail = await create(db, buyer, kt, key='next', spread='0.10')
        return buyer, kt, recipient, root, tail
    buyer, kt, recipient, root, tail = await scenario(finance, '0.15')
    other = await scenario(reference_finance, '0.30')
    receive, pay = legs()
    result = await finance['app'].trading.correct(**common(finance, 'correct'), deal_id=root,
        expected_deal_id=root, reason='spread correction', replacement={'mode': 'wallet',
            'receive': receive, 'pay': pay, 'counterparty_client_id': kt, 'counterparty_spread': '0.30'})
    assert len(result['recalculated_operations']) == 2
    assert await wallet_state(finance, [buyer, kt, recipient]) == await wallet_state(reference_finance, list(other[:3]))
    assert await total_profit(finance) == await total_profit(reference_finance)
    active = (await finance['app'].trading_queries.exchange_context(tail))['active']
    assert Decimal(active['attributes']['counterparty_spread']) == Decimal('0.10')
    assert await finance['app'].trading.correct(**common(finance, 'correct'), deal_id=root,
        expected_deal_id=root, reason='spread correction', replacement={'mode':'wallet','receive':receive,
            'pay':pay,'counterparty_client_id':kt,'counterparty_spread':'0.30'}) == result
    await assert_consistent(finance)


async def test_correction_can_remove_commission(finance):
    buyer, kt = await setup_clients(finance)
    root = await create(finance, buyer, kt)
    receive, pay = legs()
    await finance['app'].trading.correct(**common(finance, 'remove'), deal_id=root, reason='no KT',
        replacement={'mode':'wallet', 'receive':receive, 'pay':pay})
    assert await kt_balance(finance, kt) == 0
    assert await total_profit(finance) == 0


@pytest.mark.parametrize('spread,with_client', [('0.15',False), ('-0.1',True), ('0',True)])
async def test_invalid_counterparty_contract_does_not_change_money(finance, spread, with_client):
    buyer, kt = await setup_clients(finance)
    with pytest.raises(InvalidPosting):
        await create(finance, buyer, kt if with_client else None, spread=spread)
    assert await kt_balance(finance, kt) == 0
    assert await finance['pool'].fetchval('SELECT count(*) FROM journal_lines') == 0


async def test_counterparty_needs_active_chat(finance):
    buyer = await client(finance,'buyer')
    kt = await client(finance,'no chat')
    with pytest.raises(InvalidPosting,match='чат'):
        await create(finance,buyer,kt)
    assert await finance['app'].trading_queries.counterparties() == []


async def test_api_wallet_and_confirmed_cash_exchange(finance):
    from httpx import ASGITransport, AsyncClient

    from api.auth import create_access_token
    from api.thai_finance import create_thai_finance_api
    buyer, kt = await setup_clients(finance)
    app = create_thai_finance_api(container=finance['app'], jwt_secret='x' * 32)
    headers = {'Authorization':'Bearer ' + create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)}
    receive, pay = legs()
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers=headers) as http:
        choices = await http.get('/api/v1/deals/counterparties')
        assert choices.status_code == 200
        assert choices.json()['items'] == [{'id':kt,'name':'KT chat'}]
        payload = {'idempotency_key':'api-wallet','mode':'wallet','client_id':buyer,
                   'receive':receive,'pay':pay,'counterparty_client_id':kt,'counterparty_spread':'0.15'}
        created = await http.post('/api/v1/deals',json=payload)
        assert created.status_code == 200, created.text
        assert (await http.post('/api/v1/deals',json=payload)).json()['deal_id'] == created.json()['deal_id']
        root = created.json()['deal_id']
        changed = await http.post(f'/api/v1/deals/{root}/correction',json={
            'idempotency_key':'api-correction','effective_at':common(finance,'')['effective_at'].isoformat(),'expected_deal_id':root,'reason':'change KT spread',
            'replacement':{'mode':'wallet','receive':receive,'pay':pay,
                'counterparty_client_id':kt,'counterparty_spread':'0.30'}})
        assert changed.status_code == 200, changed.text
        assert await kt_balance(finance,kt) == Decimal('9.063444')
        await fund(finance,instrument='USDT',quantity=Decimal('1000'))
        payload.update(idempotency_key='api-cash',mode='cash')
        cash = await http.post('/api/v1/deals',json=payload)
        assert cash.status_code == 200, cash.text
        cash_id = cash.json()['deal_id']
        ready = await http.post(f'/api/v1/deals/{cash_id}/ready',json={'idempotency_key':'cash-ready'})
        assert ready.status_code == 200, ready.text
        for n,(code,amount) in enumerate((('THB_CASH','33100'),('USDT','-1000'))):
            result = await http.post(f'/api/v1/cash-requests/{cash_id}/settle',json={
                'idempotency_key':f'settle-{n}','instrument':code,'signed_quantity':amount})
            assert result.status_code == 200, result.text
            if n == 0:
                assert await kt_balance(finance,kt) == Decimal('9.063444')
        assert await kt_balance(finance,kt) == Decimal('13.595166')
    await assert_consistent(finance)


async def test_changing_recipient_includes_new_counterparty_history(finance):
    buyer, kt = await setup_clients(finance)
    new_kt = await client(finance,'new KT')
    await finance['app'].clients.bind_chat(key='new-kt-chat',actor_id=finance['actor'],client_id=new_kt,chat_id=-5533)
    root = await create(finance,buyer,kt)
    await finance['app'].wallet_transfers.transfer(**common(finance,'new-kt-spend'),
        source_client_id=new_kt,target_client_id=buyer,instrument='USDT',quantity='1')
    receive, pay = legs()
    result = await finance['app'].trading.correct(**common(finance,'change-kt'),deal_id=root,reason='wrong KT',
        replacement={'mode':'wallet','receive':receive,'pay':pay,
            'counterparty_client_id':new_kt,'counterparty_spread':'0.15'})
    assert len(result['recalculated_operations']) == 1
    assert await kt_balance(finance,kt) == 0
    assert await kt_balance(finance,new_kt) == Decimal('3.531722')
    assert await total_profit(finance) == Decimal('-4.531722')
    await assert_consistent(finance)


async def test_profit_closure_distributes_only_net_and_closed_correction_is_atomic(finance):
    from datetime import UTC, date, datetime

    from domain.finance.models import ReversalBlocked
    from tests.thai.test_posting import INSTANT
    buyer, kt = await setup_clients(finance)
    await finance['app'].manual_profit.record(key='previous-profit', actor_id=finance['actor'],
        amount_usdt='100',effective_at=INSTANT)
    root = await create(finance,buyer,kt)
    period = date(2026,10,1)
    assert (await finance['app'].profit_queries.report(period))['net_profit_usdt'] == Decimal('95.468278')
    await finance['app'].profit.close(key='close-month',actor_id=finance['actor'],period=period,
        effective_at=datetime(2026,11,2,tzinfo=UTC))
    closure = await finance['app'].profit_queries.closure(period)
    assert closure['net_profit_usdt'] == Decimal('95.468278')
    assert sum(row['amount_usdt'] for row in closure['accruals']) == Decimal('95.468278')
    receive,pay = legs()
    with pytest.raises(ReversalBlocked, match='profit_closure'):
        await finance['app'].trading.correct(**common(finance,'closed-correction'),deal_id=root,reason='closed',
            replacement={'mode':'wallet','receive':receive,'pay':pay,
                'counterparty_client_id':kt,'counterparty_spread':'0.30'})
    assert await kt_balance(finance,kt) == Decimal('4.531722')
    assert (await finance['app'].trading_queries.exchange_context(root))['active']['id'] == root


async def test_bulk_purchase_33_6_then_sell_thb_for_1000_usdt(finance):
    from tests.thai.test_posting import DAY, balance
    _, kt = await setup_clients(finance)
    await fund(finance, instrument='USDT', quantity=Decimal('10000'))
    trading = finance['app'].trading

    async def cash_exchange(key, receive, pay, **options):
        draft = await trading.create(key=key,actor_id=finance['actor'],client_id=None,
            mode='cash',receive=receive,pay=pay,**options)
        await trading.ready(key=key+':ready',actor_id=finance['actor'],deal_id=draft['deal_id'])
        await trading.post(**common(finance,key+':post'),deal_id=draft['deal_id'])
        return (await finance['app'].trading_queries.detail(draft['deal_id']))['attributes']

    await cash_exchange('bulk-purchase',{'instrument':'THB_CASH','quantity':'336000'},
        {'instrument':'USDT','quantity':'10000'})
    result = await cash_exchange('sell-thb',{'instrument':'USDT','quantity':'1000'},
        {'instrument':'THB_CASH','quantity':'33100'},counterparty_client_id=kt,counterparty_spread='0.15')
    assert await balance(finance,'THB_CASH') == (Decimal('302900'),Decimal('9014.88095238'))
    assert await balance(finance,'USDT') == (Decimal('1000'),Decimal('1000'))
    assert await kt_balance(finance,kt) == Decimal('4.531722')
    assert Decimal(result['result_usdt']) == Decimal('10.34923038')
    assert (await finance['app'].profit_queries.report(DAY.replace(day=1)))['net_profit_usdt'] == Decimal('10.34923038')
    assert Decimal(result['counterparty']['company_rate']) == Decimal('32.95')
    await assert_consistent(finance)


async def test_counterparty_spread_cannot_exceed_rate_when_selling_baht(finance):
    buyer, kt = await setup_clients(finance)
    with pytest.raises(InvalidPosting,match='Спред'):
        await create(finance,buyer,kt,purchase=True,spread='34')
    assert await kt_balance(finance,kt) == 0
    assert await finance['pool'].fetchval('SELECT count(*) FROM journal_lines') == 0
