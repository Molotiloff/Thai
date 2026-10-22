
import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from app.thai_russian_commands import HELP
from db_asyncpg.internal_rates import InternalRates
from tests.thai.test_expenses import common
from tests.thai.test_posting import INSTANT


async def exchange(finance,client,key,receive,quantity,pay,pay_quantity,*,instant=INSTANT):
    service=finance['app'].trading
    deal=await service.create(key=key+':create',actor_id=finance['actor'],client_id=client,mode='wallet',
        receive={'instrument':receive,'quantity':quantity},pay={'instrument':pay,'quantity':pay_quantity})
    await service.ready(key=key+':ready',actor_id=finance['actor'],deal_id=deal['deal_id'])
    await service.post(**{**common(finance,key),'business_date':instant.date(),'effective_at':instant},deal_id=deal['deal_id'])
    return deal['deal_id']


async def customer(finance,name='client'):
    return (await finance['app'].clients.create_client(key=name,actor_id=finance['actor'],name=name))['client_id']


async def test_weighted_rates_include_both_directions_and_exclude_reversal(finance):
    cid=await customer(finance)
    await exchange(finance,cid,'one','USDT','100','THB_CASH','3300')
    other=await exchange(finance,cid,'two','THB_CASH','3500','USDT','100')
    quote=await InternalRates(finance['pool']).quote('THB_CASH',INSTANT)
    assert quote.quoted_rate==34
    await finance['app'].trading.reverse(**common(finance,'undo'),deal_id=other,reason='Correction')
    assert (await InternalRates(finance['pool']).quote('THB_CASH',INSTANT)).quoted_rate==33


@pytest.mark.parametrize('instrument',['THB_CASH','THB_BANK','USD_WH','USD_BL','EUR','EUR500','RUB','USDT'])
async def test_manual_wallet_corrections_need_no_rate_positive_negative_retry(finance,instrument):
    cid=await customer(finance)
    service=finance['app'].wallet_interactions
    command={**common(finance,'adjust'),'client_id':cid,'instrument':instrument,'quantity':'100','comment':'test'}
    posted=await service.accounting_adjust(**command)
    assert await service.accounting_adjust(**command)==posted
    await service.accounting_adjust(**{**command,'key':'adjust-minus','quantity':'-150'})
    wallet=next(w for w in await finance['app'].client_queries.wallets(cid) if w['instrument_code']==instrument)
    assert wallet['balance']==-50
    assert wallet['carrying_usdt']==(-50 if instrument=='USDT' else 0)
    assert all(row['balance']==0 for row in await finance['app'].queries.cash_balances())


@pytest.mark.parametrize('instrument',['THB_CASH','THB_BANK','USD_WH','USD_BL','EUR','EUR500','RUB'])
async def test_deposit_before_first_exchange_then_uses_internal_rate(finance,instrument):
    cid=await customer(finance)
    await finance['app'].wallets.deposit(**common(finance,'deposit'),client_id=cid,instrument=instrument,quantity='100')
    wallet=next(w for w in await finance['app'].client_queries.wallets(cid) if w['instrument_code']==instrument)
    assert wallet['balance']==100 and wallet['carrying_usdt']==0
    await exchange(finance,cid,'trade',instrument,'100','USDT','10')
    other=await customer(finance,'other')
    await finance['app'].wallets.deposit(**common(finance,'deposit2'),client_id=other,instrument=instrument,quantity='100')
    wallet=next(w for w in await finance['app'].client_queries.wallets(other) if w['instrument_code']==instrument)
    assert wallet['carrying_usdt']==10


async def test_manual_rate_commands_and_api_are_removed(finance):
    from app.thai_telegram import ThaiTelegramCommands
    from domain.finance.models import InvalidPosting
    commands=ThaiTelegramCommands(finance['app'],finance['config'])
    for command in ('/курс бат 35','/курсы'):
        with pytest.raises(InvalidPosting,match='Неизвестная команда'):
            await commands.execute(telegram_user_id=12345,chat_id=-123,message_id=1,text=command,effective_at=INSTANT)
    assert '/курс' not in HELP
    app=create_thai_finance_api(container=finance['app'],jwt_secret='x'*32)
    headers={'Authorization':'Bearer '+create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)}
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers=headers) as http:
        assert (await http.get('/api/v1/rates')).status_code==404
        assert (await http.post('/api/v1/rates',json={})).status_code==404


async def seed_internal_rates(finance):
    """Prepare cross-currency scenarios with real completed exchanges, leaving cash untouched."""
    from decimal import Decimal
    cid=await customer(finance,'rate-provider')
    for code,rate in (('THB_CASH','35'),('THB_BANK','35'),('USD_BL','1'),('USD_WH','1.01'),
                      ('EUR','1.1'),('EUR500','1.2'),('RUB','0.01')):
        quantity='3500' if code.startswith('THB') else '100'
        usdt='100' if code.startswith('THB') else str(Decimal(quantity)*Decimal(rate))
        await exchange(finance,cid,'seed:'+code,code,quantity,'USDT',usdt)


async def test_retry_keeps_original_rate_after_new_exchange(finance):
    cid=await customer(finance)
    service=finance['app'].wallet_interactions
    args={**common(finance,'adjust'),'client_id':cid,'instrument':'THB_CASH','quantity':'100'}
    first=await service.accounting_adjust(**args)
    await exchange(finance,cid,'trade','THB_CASH','3500','USDT','100')
    assert await service.accounting_adjust(**args)==first
    deposit={**common(finance,'deposit'),'client_id':cid,'instrument':'THB_CASH','quantity':'350'}
    cash=await finance['app'].wallets.deposit(**deposit)
    await exchange(finance,cid,'trade2','USDT','100','THB_CASH','3700')
    assert await finance['app'].wallets.deposit(**deposit)==cash
    assert (await InternalRates(finance['pool']).quote('THB_CASH',INSTANT)).quoted_rate==36


async def test_weighting_uses_volume_not_mean_of_quotes(finance):
    cid=await customer(finance)
    await exchange(finance,cid,'large','THB_CASH','9900','USDT','300')
    await exchange(finance,cid,'small','USDT','100','THB_CASH','3500')
    assert (await InternalRates(finance['pool']).quote('THB_CASH',INSTANT)).quoted_rate==33.5


@pytest.mark.parametrize('instrument',['THB_CASH','THB_BANK','USD_WH','USD_BL','EUR','EUR500','RUB'])
async def test_unvalued_deposit_can_be_returned_without_rate(finance,instrument):
    cid=await customer(finance)
    await finance['app'].wallets.deposit(**common(finance,'deposit'),client_id=cid,instrument=instrument,quantity='100')
    await finance['app'].wallets.withdraw(**common(finance,'return'),client_id=cid,instrument=instrument,quantity='100')
    wallet=next(w for w in await finance['app'].client_queries.wallets(cid) if w['instrument_code']==instrument)
    assert wallet['balance']==wallet['carrying_usdt']==wallet['unvalued_quantity']==0
    assert all(row['balance']==row['carrying_usdt']==0 for row in await finance['app'].queries.cash_balances())


async def test_future_and_draft_exchanges_do_not_set_internal_rate(finance):
    from datetime import timedelta
    cid=await customer(finance)
    await finance['app'].trading.create(key='draft',actor_id=finance['actor'],client_id=cid,mode='wallet',
        receive={'instrument':'THB_CASH','quantity':'5000'},pay={'instrument':'USDT','quantity':'100'})
    await exchange(finance,cid,'future','THB_CASH','4500','USDT','100',instant=INSTANT+timedelta(days=1))
    assert await InternalRates(finance['pool']).quote('THB_CASH',INSTANT) is None
    await exchange(finance,cid,'now','USDT','100','THB_CASH','3500')
    assert (await InternalRates(finance['pool']).quote('THB_CASH',INSTANT)).quoted_rate==35
