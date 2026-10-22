import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from app.thai_telegram import ThaiTelegramCommands, ThaiTelegramDelivery
from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository
from domain.finance.models import InsufficientCash, InvalidPosting
from services.cash_requests.constants import ACCOUNTING_FX_CMD_MAP
from tests.thai.test_posting import DAY, INSTANT, balance


def common(finance,key):
    return {'key':key,'actor_id':finance['actor'],'business_date':DAY,'effective_at':INSTANT}


async def send(commands,text,mid):
    return await commands.execute(telegram_user_id=12345,chat_id=-123,message_id=mid,text=text,
        effective_at=INSTANT,chat_name='Клиент <А>',actor_name='Менеджер <Б>')


async def prepare(finance, *, fund=True):
    commands = ThaiTelegramCommands(finance['app'],finance['config'])
    await send(commands,'/start',1)
    cid = await finance['app'].trading_queries.client_by_chat(-123)
    for _n,(code,rate) in enumerate((('THB_CASH','35'),('THB_BANK','35'),('USDT','1'),
        ('USD_BL','1'),('USD_WH','1.01'),('EUR','1.1'),('EUR500','1.1'),('RUB','0.01'))):
        if fund:
            await finance['app'].wallets.deposit(**common(finance,f'fund-{code}'),client_id=cid,
                instrument=code,quantity='10000',quoted_rate=rate)
    return commands,cid


async def ready(finance,deal_id):
    await finance['app'].cash_requests.change_request_status(key=f'ready-{deal_id}',actor_id=finance['actor'],
        request_id=f'Б-{deal_id:06d}',action='ready')


async def test_all_56_cash_directions_confirm_twice_post_once_and_preserve_wallet(finance):
    commands,cid = await prepare(finance)
    from tests.thai.test_internal_exchange_rates import seed_internal_rates
    await seed_internal_rates(finance)
    baseline=await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind='fx_trade'")
    wallet_before = await finance['app'].client_queries.wallets(cid)
    for n,(command,(_,incoming,outgoing)) in enumerate(ACCOUNTING_FX_CMD_MAP.items()):
        mid = 1000+n*4
        card = await send(commands,f'/{command} 100.25 50.12 @cashier @client ! <обмен>',mid)
        assert 'Заявка на обмен' in card and '&lt;обмен&gt;' in card and 'Город' not in card
        deal_id = await commands.cash_request_by_command(-123,mid)
        request_id = f'Б-{deal_id:06d}'
        before = {code:await balance(finance,code) for code in (incoming,outgoing)}
        await ready(finance,deal_id)
        first = await send(commands,f'/{incoming} 100.25 {request_id}',mid+1)
        assert 'сторона подтверждена' in first
        assert {code:await balance(finance,code) for code in before}==before
        result = await send(commands,f'/{outgoing} -50.12 {request_id}',mid+2)
        assert 'проведена: операция' in result
        assert await send(commands,f'/{outgoing} -50.12 {request_id}',mid+3)==result
        assert (await balance(finance,incoming))[0]==before[incoming][0]+Decimal('100.25')
        assert (await balance(finance,outgoing))[0]==before[outgoing][0]-Decimal('50.12')
        detail = await finance['app'].trading_queries.detail(deal_id)
        assert detail['effective_status']=='posted' and len(detail['cash_confirmations'])==2
        assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind='fx_trade'")==baseline+n+1
    assert await finance['app'].client_queries.wallets(cid)==wallet_before


async def test_insufficient_cash_rolls_back_second_confirmation_then_retry_works(finance):
    commands,cid = await prepare(finance,fund=False)
    await send(commands,'/пбатвт 3500 100',100)
    await ready(finance,1)
    await send(commands,'/бат 3500 Б-000001',101)
    with pytest.raises(InsufficientCash):
        await send(commands,'/USDT -100 Б-000001',102)
    assert await balance(finance,'THB_CASH')==(0,0)
    assert len((await finance['app'].trading_queries.detail(1))['cash_confirmations'])==1
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind='fx_trade'")==0
    await finance['app'].wallets.deposit(**common(finance,'fund'),client_id=cid,instrument='USDT',quantity='100',quoted_rate='1')
    assert 'проведена' in await send(commands,'/USDT -100 Б-000001',102)
    assert (await balance(finance,'THB_CASH'))[0]==3500
    assert (await balance(finance,'USDT'))[0]==0


async def test_cash_exchange_edit_cancel_and_wrong_actuals_do_not_move_money(finance):
    commands,_ = await prepare(finance)
    await send(commands,'/птвбат 100 3500',100)
    original = await finance['app'].trading_queries.detail(1)
    await commands.edit_cash_request(telegram_user_id=12345,chat_id=-123,message_id=101,request_id=1,
        text='/птвбат 110 3850 ! правка',actor_name='Редактор',effective_at=INSTANT)
    edited = await finance['app'].trading_queries.detail(1)
    assert edited['attributes']['pin_code']==original['attributes']['pin_code']
    assert edited['attributes']['receive']['quantity']=='110.000000'
    await ready(finance,1)
    for n,text in enumerate(('/USDT -110 Б-000001','/USDT 111 Б-000001','/EUR 110 Б-000001')):
        with pytest.raises(InvalidPosting,match='не совпадают'):
            await send(commands,text,105+n)
    await send(commands,'/бат -3850 Б-000001',110)
    await send(commands,'/бат -3850 Б-000001',111)
    with pytest.raises(InvalidPosting,match='обе стороны'):
        await finance['app'].trading.post(**common(finance,'bypass'),deal_id=1)
    await finance['app'].cash_requests.change_request_status(key='cancel',actor_id=finance['actor'],request_id='Б-000001',action='cancel')
    with pytest.raises(InvalidPosting):
        await send(commands,'/USDT 110 Б-000001',112)
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind='fx_trade'")==0
    assert len((await finance['app'].trading_queries.detail(1))['cash_confirmations'])==1


async def test_cash_exchange_concurrent_last_side_posts_once_and_storno_updates_card(finance):
    commands,_ = await prepare(finance)
    await send(commands,'/пбатвт 3500 100',100)
    await ready(finance,1)
    await send(commands,'/USDT -100 Б-000001',101)
    results = await asyncio.gather(send(commands,'/бат 3500 Б-000001',102),send(commands,'/бат 3500 Б-000001',103))
    assert results[0]==results[1]
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind='fx_trade'")==1
    telegram = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)),edit_message_text=AsyncMock())
    delivery = ThaiTelegramDelivery(finance['app'],telegram,None)
    await delivery.deliver_cash_request(1)
    operation_id = (await finance['app'].trading_queries.detail(1))['operation_id']
    await send(commands,f'/сторно {operation_id} Ошибка',104)
    while event := await OutboxDeliveryRepository(finance['pool']).claim():
        await delivery.deliver(event)
        await OutboxDeliveryRepository(finance['pool']).delivered(event)
    assert telegram.edit_message_text.call_args.kwargs['reply_markup'] is None
    assert 'отменена' in telegram.edit_message_text.call_args.args[0].lower()
    assert (await balance(finance,'THB_CASH'))[0]==10000 and (await balance(finance,'USDT'))[0]==10000


async def test_crm_cash_exchange_uses_same_two_confirmation_flow(finance):
    _,cid = await prepare(finance)
    api = create_thai_finance_api(container=finance['app'],jwt_secret='x'*32)
    token = create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test',headers={'Authorization':'Bearer '+token}) as http:
        result = await http.post('/api/v1/deals',json={'idempotency_key':'crm-cash','client_id':cid,'mode':'cash',
            'receive':{'instrument':'THB_BANK','quantity':'3500','quoted_rate':'35'},
            'pay':{'instrument':'USDT','quantity':'100','quoted_rate':'1'},'comment':'Кассовая заявка CRM'})
        assert result.status_code==200,result.text
        deal_id = result.json()['deal_id']
        assert result.json()['external_number']==result.json()['request_id']==f'Б-{deal_id:06d}'
        assert (await http.get(f'/api/v1/deals/{deal_id}')).json()['attributes']['comment']=='Кассовая заявка CRM'
        assert (await http.post(f'/api/v1/deals/{deal_id}/ready',json={'idempotency_key':'crm-ready'})).status_code==200
        before = await balance(finance,'THB_BANK')
        for n,(instrument,quantity) in enumerate((('USDT','-100'),('THB_BANK','3500'))):
            result = await http.post(f'/api/v1/cash-requests/{deal_id}/settle',json={'idempotency_key':f'confirm-{n}',
                'effective_at':INSTANT.isoformat(),'instrument':instrument,'signed_quantity':quantity})
            assert result.status_code==200,result.text
            if n==0:
                assert result.json()['operation_id'] is None and await balance(finance,'THB_BANK')==before
        assert result.json()['operation_id'] is not None
        assert (await balance(finance,'THB_BANK'))[0]==13500
