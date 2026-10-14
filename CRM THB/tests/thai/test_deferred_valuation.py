"""Client THB can settle historical debt or remain unpriced in the common cash pool."""
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from app.thai_telegram import ThaiTelegramCommands
from tests.thai.test_posting import INSTANT, balance, fund


def common(finance,key):
    return {'key':key,'actor_id':finance['actor'],'business_date':INSTANT.date(),'effective_at':INSTANT}


async def client(finance,name='Client',chat=-123):
    return (await finance['app'].clients.create_client(key=f'client:{chat}',actor_id=finance['actor'],name=name,chat_id=chat))['client_id']


async def deposit(finance,client_id,quantity,*,key='deposit',instrument='THB_CASH'):
    return await finance['app'].wallets.deposit(**common(finance,key),client_id=client_id,instrument=instrument,quantity=quantity)


async def wallet(finance,client_id,instrument='THB_CASH'):
    return next(row for row in await finance['app'].client_queries.wallets(client_id) if row['instrument_code']==instrument)


async def exchange(finance,client_id,receive,pay,*,key='exchange'):
    app=finance['app']
    draft=await app.trading.create(key=key+':draft',actor_id=finance['actor'],client_id=client_id,
        mode='wallet',receive=receive,pay=pay)
    await app.trading.ready(key=key+':ready',actor_id=finance['actor'],deal_id=draft['deal_id'])
    return await app.trading.post(**common(finance,key),deal_id=draft['deal_id'])


@pytest.mark.parametrize('instrument',['THB_CASH','THB_BANK'])
async def test_exchange_debt_is_settled_at_its_historical_cost_without_current_rate(finance,instrument):
    customer=await client(finance)
    await exchange(finance,customer,{'instrument':instrument,'quantity':'3500'}, {'instrument':'USDT','quantity':'100'})
    row=await wallet(finance,customer,instrument)
    assert row['balance']==-3500 and row['carrying_usdt']==-100
    rates=await finance['pool'].fetchval('SELECT count(*) FROM fx_rate_snapshots')
    await deposit(finance,customer,'1750',key='part',instrument=instrument)
    assert (await wallet(finance,customer,instrument))['carrying_usdt']==-50
    await deposit(finance,customer,'2500',key='rest',instrument=instrument)
    row=await wallet(finance,customer,instrument)
    assert row['balance']==750 and row['carrying_usdt']==0 and row['unvalued_quantity']==750
    assert await balance(finance,instrument)==(4250,100)
    assert await finance['pool'].fetchval('SELECT count(*) FROM fx_rate_snapshots')==rates
    assert await finance['pool'].fetchval("SELECT coalesce(sum(l.amount_usdt),0) FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id WHERE a.purpose IN ('fx_income','fx_loss')")==0


async def test_unpriced_deposit_and_partial_full_refund_need_no_rate_or_profit(finance):
    customer=await client(finance)
    original=await deposit(finance,customer,'3500')
    assert await deposit(finance,customer,'3500')==original
    row=await wallet(finance,customer)
    assert (row['balance'],row['carrying_usdt'],row['unvalued_quantity'])==(3500,0,3500)
    for key,quantity in (('first','1500'),('rest','2000')):
        await finance['app'].wallets.withdraw(**common(finance,key),client_id=customer,instrument='THB_CASH',quantity=quantity)
    assert await balance(finance,'THB_CASH')==(0,0)
    assert (await wallet(finance,customer))['unvalued_quantity']==0
    assert await finance['pool'].fetchval('SELECT count(*) FROM fx_rate_snapshots')==0
    assert await finance['pool'].fetchval("SELECT coalesce(sum(l.amount_usdt),0) FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id WHERE a.account_class IN ('income','expense')")==0


async def test_deposit_then_partial_exchange_prices_funds_and_reversal_restores_pending(finance):
    customer=await client(finance)
    await deposit(finance,customer,'3500')
    trade=await exchange(finance,customer,{'instrument':'THB_CASH','quantity':'1750'},{'instrument':'USDT','quantity':'50'})
    assert await balance(finance,'THB_CASH')==(3500,100)
    row=await wallet(finance,customer)
    assert (row['balance'],row['carrying_usdt'],row['unvalued_quantity'])==(1750,50,0)
    assert await finance['pool'].fetchval("SELECT count(*) FROM journal_lines WHERE operation_id=$1 AND valuation_component",trade.id)==4
    await finance['app'].trading.reverse(**common(finance,'reverse'),deal_id=1,reason='Test')
    row=await wallet(finance,customer)
    assert (row['balance'],row['carrying_usdt'],row['unvalued_quantity'])==(3500,0,3500)
    assert await balance(finance,'THB_CASH')==(3500,0)
    await finance['app'].projections.rebuild(finance['actor'])
    assert await finance['pool'].fetchval("SELECT unvalued_quantity FROM inventory_balances WHERE instrument_code='THB_CASH'")==3500


async def test_common_pool_can_pay_another_customer_after_exchange(finance):
    depositor=await client(finance)
    recipient=await client(finance,chat=-124)
    await deposit(finance,depositor,'3500')
    await exchange(finance,recipient,{'instrument':'USDT','quantity':'50'},{'instrument':'THB_CASH','quantity':'1750'})
    await finance['app'].wallets.withdraw(**common(finance,'pay'),client_id=recipient,instrument='THB_CASH',quantity='1750')
    assert await balance(finance,'THB_CASH')==(1750,50)
    row=await wallet(finance,depositor)
    assert row['balance']==3500 and row['carrying_usdt']==100 and row['unvalued_quantity']==0
    assert (await wallet(finance,recipient))['balance']==0


async def test_expense_uses_unpriced_common_pool_at_expense_rate(finance):
    customer=await client(finance)
    await deposit(finance,customer,'3500')
    category=await finance['pool'].fetchval('SELECT id FROM expense_categories ORDER BY id LIMIT 1')
    expense=await finance['app'].expenses.record(**common(finance,'expense'),category_id=category,
        instrument='THB_CASH',amount='350',quoted_rate='35',payer='business',shares={'common':'100'})
    assert await balance(finance,'THB_CASH')==(3150,90)
    row=await wallet(finance,customer)
    assert row['balance']==3500 and row['carrying_usdt']==100 and row['unvalued_quantity']==0
    assert await finance['pool'].fetchval("SELECT coalesce(sum(l.amount_usdt),0) FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id WHERE l.operation_id=$1 AND a.purpose='operating_expense'",expense.id)==10


async def test_cash_settlement_api_uses_server_now_and_workspace_actual_date(finance,monkeypatch):
    now=datetime(2026,10,15,18,1,tzinfo=UTC) # 16 October in Bangkok.
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return now
    monkeypatch.setattr('api.thai_trading.datetime',Clock)
    commands=ThaiTelegramCommands(finance['app'],finance['config'])
    await commands.execute(telegram_user_id=12345,chat_id=-123,message_id=1,text='/депбат 1000',
        effective_at=INSTANT,chat_name='Client')
    await finance['app'].cash_requests.change_request_status(key='ready',actor_id=finance['actor'],request_id='Б-000001',action='ready')
    api=create_thai_finance_api(container=finance['app'],jwt_secret='x'*32)
    token=create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test',headers={'Authorization':'Bearer '+token}) as http:
        body={'idempotency_key':'settle','instrument':'THB_CASH','signed_quantity':'1000',
            'effective_at':'2001-01-01T00:00:00Z','business_date':'2001-01-01'}
        response=await http.post('/api/v1/cash-requests/1/settle',json=body)
        assert response.status_code==200,response.text
        operation_id=response.json()['id']
        operation=await finance['pool'].fetchrow('SELECT business_date,effective_at FROM financial_operations WHERE id=$1',operation_id)
        assert operation['effective_at']==now and operation['business_date'].isoformat()=='2026-10-16'
        assert (await http.post('/api/v1/cash-requests/1/settle',json=body)).json()['id']==operation_id
        item=(await http.get('/api/v1/deals/workspace?kind=deposit&status=posted')).json()['items'][0]
        assert datetime.fromisoformat(item['created_at'])==now


async def test_pending_deposit_does_not_dilute_priced_inventory_and_later_debt_payment(finance):
    depositor=await client(finance)
    await fund(finance,instrument='THB_CASH',quantity=Decimal('700')) # 20 USDT at 35.
    await deposit(finance,depositor,'3500')
    assert await balance(finance,'THB_CASH')==(4200,20)
    assert (await wallet(finance,depositor))['unvalued_quantity']==3500
    # A priced customer debt is settled without re-pricing other people's deposits.
    debtor=await client(finance,chat=-125)
    await exchange(finance,debtor,{'instrument':'THB_CASH','quantity':'350'},{'instrument':'USDT','quantity':'10'})
    # The exchange establishes book value for pooled pending funds at its rate.
    assert await balance(finance,'THB_CASH')==(4200,120)
    before=await balance(finance,'THB_CASH')
    await deposit(finance,debtor,'350',key='settle-debt')
    after=await balance(finance,'THB_CASH')
    assert (after[0]-before[0],after[1]-before[1])==(350,10)
    assert (await wallet(finance,debtor))['balance']==0


async def test_failed_exchange_rolls_back_valuation_and_concurrent_deposits_are_not_lost(finance):
    import asyncio

    from domain.finance.models import InsufficientCash
    customer=await client(finance)
    await asyncio.gather(deposit(finance,customer,'1000',key='a'),deposit(finance,customer,'2500',key='b'))
    assert (await wallet(finance,customer))['unvalued_quantity']==3500
    created=await finance['app'].trading.create(key='cash-draft',actor_id=finance['actor'],client_id=customer,
        mode='cash',receive={'instrument':'USDT','quantity':'100'},
        pay={'instrument':'THB_CASH','quantity':'7000','quoted_rate':'35'})
    await finance['app'].trading.ready(key='cash-ready',actor_id=finance['actor'],deal_id=created['deal_id'])
    with pytest.raises(InsufficientCash):
        await finance['app'].trading.post(**common(finance,'fail'),deal_id=created['deal_id'])
    assert (await wallet(finance,customer))['unvalued_quantity']==3500
    assert await balance(finance,'THB_CASH')==(3500,0)
    assert await finance['pool'].fetchval('SELECT count(*) FROM fx_rate_snapshots')==0
