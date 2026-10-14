from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from app.thai_telegram import ThaiTelegramCommands, ThaiTelegramDelivery
from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository
from domain.finance.models import (
    IdempotencyConflict,
    InsufficientCash,
    InvalidPosting,
    PeriodClosed,
    ReversalBlocked,
)
from tests.thai.test_posting import INSTANT, balance, fund


async def setup(finance, *, operations_chat=-999):
    commands = ThaiTelegramCommands(finance['app'],replace(finance['config'],operations_chat_id=operations_chat))
    for mid,text in ((1,'/start'),(2,'/пт 100 обат 3300 первоначальная')):
        await commands.execute(telegram_user_id=12345,chat_id=-123,message_id=mid,text=text,
            effective_at=INSTANT,chat_name='Клиент <А>',actor_name='Автор <Б>')
    telegram = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)),
        edit_message_text=AsyncMock())
    delivery = ThaiTelegramDelivery(finance['app'],telegram,operations_chat)
    event = await OutboxDeliveryRepository(finance['pool']).claim()
    await delivery.deliver(event)
    return commands,telegram,delivery,event


async def edit(commands, text='/пт 120 обезнал 4200 исправлено', *, mid=3, chat_id=-123):
    return await commands.edit_exchange(telegram_user_id=12345,chat_id=chat_id,message_id=mid,
        reply_message_id=900,text=text,actor_name='Редактор',effective_at=INSTANT)


async def wallets(finance):
    client_id = await finance['app'].trading_queries.client_by_chat(-123)
    return {r['instrument_code']:r['balance'] for r in await finance['app'].client_queries.wallets(client_id)}


async def test_exchange_edit_is_atomic_and_retry_keeps_same_request(finance):
    commands,telegram,delivery,event = await setup(finance)
    result = await edit(commands)
    assert result['request_id']==1 and result['deal_id']==2
    assert await edit(commands)==result
    with pytest.raises(IdempotencyConflict):
        await edit(commands,'/пт 130 обезнал 4500 другое')
    values = await wallets(finance)
    assert values['USDT']==-120 and values['THB_CASH']==0 and values['THB_BANK']==4200
    assert all(row['balance']==0 for row in await finance['app'].queries.cash_balances())
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==3
    context = await finance['app'].trading_queries.exchange_context(2)
    assert context['root']['id']==1 and context['active']['effective_status']=='posted'
    await delivery.deliver_exchange_request(1)
    assert telegram.send_message.await_count==2
    assert telegram.edit_message_text.await_count==2
    for call in telegram.edit_message_text.call_args_list:
        assert '<b>Заявка</b>: <code>1</code>' in call.args[0]
        assert 'Изменение' in call.args[0] and '4’200.00 безнал' in call.args[0]
        assert call.kwargs['reply_markup'].inline_keyboard[0][0].callback_data=='req_cancel:1:2'
    second = await edit(commands,'/пт 125 обат 4375 снова',mid=4,chat_id=-999)
    assert second['request_id']==1 and second['deal_id']==3
    values = await wallets(finance)
    assert values['USDT']==-125 and values['THB_BANK']==0 and values['THB_CASH']==4375


async def test_crm_client_purchase_posts_like_bot_and_delivers_original_cards(finance):
    app=finance['app']
    client=await app.clients.create_client(key='test-f',actor_id=finance['actor'],name='Test F',chat_id=-333)
    api=create_thai_finance_api(container=app,jwt_secret='x'*32)
    token=create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test',headers={'Authorization':'Bearer '+token}) as http:
        payload={'idempotency_key':'crm-purchase','client_id':client['client_id'],'mode':'wallet',
            'effective_at':INSTANT.isoformat(),'receive':{'instrument':'USDT','quantity':'100'},
            'pay':{'instrument':'THB_CASH','quantity':'3300'},'comment':'Из CRM'}
        result=await http.post('/api/v1/deals',json=payload)
        assert result.status_code==200,result.text
        body=result.json()
        assert body['status']=='posted' and body['notification_queued']
        assert body['external_number']==body['request_id']==str(body['deal_id'])
        assert (await http.post('/api/v1/deals',json=payload)).json()==body
        payload['pay']['quantity']='3400'
        assert (await http.post('/api/v1/deals',json=payload)).status_code==409
    balances={row['instrument_code']:row['balance'] for row in await app.client_queries.wallets(client['client_id'])}
    assert balances['USDT']==-100 and balances['THB_CASH']==3300
    assert all(row['balance']==0 for row in await app.queries.cash_balances())
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==1
    bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)),edit_message_text=AsyncMock())
    delivery=ThaiTelegramDelivery(app,bot,-999)
    event=await OutboxDeliveryRepository(finance['pool']).claim()
    await delivery.deliver(event)
    await delivery.deliver(event)
    assert {call.args[0] for call in bot.send_message.call_args_list}=={-333,-999}
    for call in bot.send_message.call_args_list:
        assert '<b>Заявка</b>: <code>1</code>' in call.args[1]
        assert '3’300.00 бат' in call.args[1]
        if call.args[0]==-999:
            assert 'Test F' in call.args[1]
    commands=ThaiTelegramCommands(app,replace(finance['config'],operations_chat_id=-999))
    await commands.cancel_exchange(telegram_user_id=12345,chat_id=-333,message_id=900,data='req_cancel:1:1')
    await delivery.deliver_exchange_request(1)
    assert bot.edit_message_text.await_count==2
    assert all(row['balance']==0 for row in await app.client_queries.wallets(client['client_id']))


async def test_crm_client_exchange_retry_without_timestamp_and_failure_are_atomic(finance):
    app=finance['app']
    client=await app.clients.create_client(key='test-f',actor_id=finance['actor'],name='Test F',chat_id=-333)
    api=create_thai_finance_api(container=app,jwt_secret='x'*32)
    token=create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test',headers={'Authorization':'Bearer '+token}) as http:
        payload={'idempotency_key':'crm-failure','client_id':client['client_id'],'mode':'wallet',
            'receive':{'instrument':'USD_BL','quantity':'100'},'pay':{'instrument':'EUR','quantity':'90'}}
        result=await http.post('/api/v1/deals',json=payload)
        assert result.status_code==422,result.text
        assert await finance['pool'].fetchval('SELECT count(*) FROM deals')==0
        assert await finance['pool'].fetchval('SELECT count(*) FROM telegram_outbox')==0
        assert await finance['pool'].fetchval("SELECT count(*) FROM client_directory_events WHERE kind='trade_create'")==0
        payload.update(idempotency_key='crm-success',receive={'instrument':'USDT','quantity':'100'},
            pay={'instrument':'THB_CASH','quantity':'3300'})
        result=await http.post('/api/v1/deals',json=payload)
        assert result.status_code==200,result.text
        assert (await http.post('/api/v1/deals',json=payload)).json()==result.json()
        assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==1


async def test_failed_replacement_rolls_back_reversal_and_metadata(finance):
    commands,_,_,_ = await setup(finance)
    before = await wallets(finance)
    with pytest.raises(InvalidPosting,match='оценка'):
        await edit(commands,'/пб 10 ое 9')
    assert await wallets(finance)==before
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==1
    assert await finance['pool'].fetchval('SELECT count(*) FROM deals')==1
    assert await finance['pool'].fetchval('SELECT count(*) FROM deal_reversals')==0
    assert await finance['pool'].fetchval("SELECT count(*) FROM client_directory_events WHERE kind='trade_correct'")==0
    assert (await finance['app'].trading_queries.detail(1))['effective_status']=='posted'


async def test_cancel_rejects_stale_forged_and_unauthorized_buttons(finance):
    commands,telegram,delivery,_ = await setup(finance)
    await edit(commands)
    with pytest.raises(InvalidPosting,match='изменена'):
        await commands.cancel_exchange(telegram_user_id=12345,chat_id=-123,message_id=900,data='req_cancel:1:1')
    with pytest.raises(InvalidPosting,match='принадлежит'):
        await commands.cancel_exchange(telegram_user_id=12345,chat_id=-777,message_id=900,data='req_cancel:1:2')
    with pytest.raises(InvalidPosting,match='Нет доступа'):
        await commands.cancel_exchange(telegram_user_id=777,chat_id=-123,message_id=900,data='req_cancel:1:2')
    await commands.cancel_exchange(telegram_user_id=12345,chat_id=-123,message_id=900,data='req_cancel:1:2')
    await commands.cancel_exchange(telegram_user_id=12345,chat_id=-999,message_id=900,data='req_cancel:1:2')
    assert all(value==0 for value in (await wallets(finance)).values())
    await delivery.deliver_exchange_request(1)
    assert telegram.send_message.await_count==2
    for call in telegram.edit_message_text.call_args_list:
        assert call.kwargs['reply_markup'] is None
        assert 'отмена' in call.args[0].lower() or 'отменена' in call.args[0].lower()
    with pytest.raises(InvalidPosting,match='отменена'):
        await edit(commands,mid=4)


async def test_subsequent_wallet_movement_blocks_historical_edit(finance):
    commands,_,_,_ = await setup(finance)
    await commands.execute(telegram_user_id=12345,chat_id=-123,message_id=7,text='/USDT 1',effective_at=INSTANT)
    before = await wallets(finance)
    with pytest.raises(ReversalBlocked,match='последующие'):
        await edit(commands)
    assert await wallets(finance)==before
    assert await finance['pool'].fetchval('SELECT count(*) FROM deal_reversals')==0


async def test_crm_correction_and_operation_reversal_update_same_request(finance):
    _,telegram,delivery,_ = await setup(finance)
    api = create_thai_finance_api(container=finance['app'],jwt_secret='x'*32)
    token = create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test',headers={'Authorization':'Bearer '+token}) as http:
        payload = {'idempotency_key':'crm-correct','effective_at':INSTANT.isoformat(),'expected_deal_id':1,
            'reason':'Исправление','replacement':{'mode':'wallet',
                'receive':{'instrument':'THB_BANK','quantity':'3500'},'pay':{'instrument':'USDT','quantity':'100'},'comment':'CRM'}}
        result = await http.post('/api/v1/deals/1/correction',json=payload)
        assert result.status_code==200,result.text
        assert (await http.post('/api/v1/deals/1/correction',json=payload)).json()==result.json()
        values = await wallets(finance)
        assert values['THB_CASH']==0 and values['THB_BANK']==-3500 and values['USDT']==100
        corrected = result.json()
        assert corrected['deal_id']==2 and corrected['request_id']==1
        result = await http.post(f"/api/v1/finance/operations/{corrected['operation_id']}/reversal",json={
            'idempotency_key':'crm-cancel','effective_at':INSTANT.isoformat(),'reason':'Отмена'})
        assert result.status_code==200,result.text
    await delivery.deliver_exchange_request(1)
    assert telegram.send_message.await_count==2
    assert all(value==0 for value in (await wallets(finance)).values())
    assert await balance(finance,'THB_CASH')==(0,0)


async def test_closed_period_rejects_exchange_edit_without_changes(finance):
    commands,_,_,_ = await setup(finance)
    before = await wallets(finance)
    await finance['pool'].execute("INSERT INTO period_locks(context,period) VALUES ('ledger','2026-10-01')")
    with pytest.raises(PeriodClosed):
        await edit(commands)
    assert await wallets(finance)==before
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==1


async def test_cash_correction_insufficient_funds_preserves_original_trade(finance):
    from decimal import Decimal

    from tests.thai.test_trading import common, trade
    await fund(finance,instrument='USDT',quantity=Decimal(100))
    deal_id = await trade(finance)
    await finance['app'].trading.post(**common(finance,'original'),deal_id=deal_id)
    before = {code:await balance(finance,code) for code in ('USDT','THB_CASH')}
    with pytest.raises(InsufficientCash):
        await finance['app'].trading.correct(**common(finance,'invalid-correction'),deal_id=deal_id,
            expected_deal_id=deal_id,reason='Правка',replacement={'mode':'cash',
                'receive':{'instrument':'THB_CASH','quantity':'3500','quoted_rate':'35'},
                'pay':{'instrument':'USDT','quantity':'200','quoted_rate':'1'}})
    assert {code:await balance(finance,code) for code in before}==before
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==2
    assert await finance['pool'].fetchval('SELECT count(*) FROM deal_reversals')==0
    assert (await finance['app'].trading_queries.detail(deal_id))['effective_status']=='posted'


async def test_concurrent_exchange_edits_reject_outdated_version(finance):
    import asyncio
    await setup(finance)
    results = await asyncio.gather(*(finance['app'].trading.correct(key=f'edit-{n}',actor_id=finance['actor'],
        deal_id=1,expected_deal_id=1,business_date=INSTANT.date(),effective_at=INSTANT,reason='Правка',
        replacement={'mode':'wallet','receive':{'instrument':'USDT','quantity':str(110+n)},
            'pay':{'instrument':'THB_BANK','quantity':'4000'}}) for n in range(2)),return_exceptions=True)
    assert sum(isinstance(r,dict) for r in results)==1
    assert sum(isinstance(r,InvalidPosting) for r in results)==1
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==3
    assert await finance['pool'].fetchval('SELECT count(*) FROM deals')==2


async def test_reply_cancel_and_old_outbox_events_do_not_restore_card(finance):
    commands,telegram,delivery,initial = await setup(finance)
    await edit(commands)
    await commands.cancel_exchange_reply(telegram_user_id=12345,chat_id=-123,reply_message_id=900)
    # Replay the original event after removing its transport acknowledgements.
    await finance['pool'].execute('DELETE FROM telegram_deliveries WHERE event_key=$1',initial.key)
    await delivery.deliver(initial)
    assert telegram.send_message.await_count==2
    assert telegram.edit_message_text.await_count==2
    for call in telegram.edit_message_text.call_args_list:
        assert call.kwargs['reply_markup'] is None
    assert all(value==0 for value in (await wallets(finance)).values())


async def test_legacy_delivered_card_is_adopted_without_new_message(finance):
    commands,telegram,delivery,_ = await setup(finance)
    await finance['pool'].execute('DELETE FROM telegram_message_links')
    await edit(commands)
    await delivery.deliver_exchange_request(1)
    assert telegram.send_message.await_count==2
    assert telegram.edit_message_text.await_count==2
    assert await finance['pool'].fetchval("SELECT count(*) FROM telegram_message_links WHERE deal_id=1")==2


async def test_existing_handler_edits_bot_reply_and_ignores_forward(finance,monkeypatch):
    from aiogram import Bot
    from aiogram.types import Chat, Message, MessageOriginUser, User

    from app.setup_handlers import create_router
    commands,_,_,_ = await setup(finance,operations_chat=None)
    answer = AsyncMock()
    monkeypatch.setattr(Message,'reply',AsyncMock())
    monkeypatch.setattr(Message,'answer',answer)
    bot = Bot('123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=901))
    bot.edit_message_text = AsyncMock()
    chat = Chat(id=-123,type='supergroup',title='Клиент')
    user = User(id=12345,is_bot=False,first_name='Менеджер')
    card = Message(message_id=900,date=INSTANT,chat=chat,from_user=User(id=bot.id,is_bot=True,first_name='Bot'),text='Карточка').as_(bot)
    message = Message(message_id=3,date=INSTANT,chat=chat,from_user=user,
        text='/пт 120 обезнал 4200',reply_to_message=card).as_(bot)
    handler = create_router(commands).message.handlers[0].callback
    try:
        await handler(message)
        assert bot.edit_message_text.await_count==1
        assert (await wallets(finance))['THB_BANK']==4200
        forwarded = message.model_copy(update={'message_id':4,'forward_origin':MessageOriginUser(
            type='user',date=INSTANT,sender_user=user)})
        await handler(forwarded)
        assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==3
        forged = message.model_copy(update={'message_id':5,'reply_to_message':card.model_copy(update={'message_id':999})})
        await handler(forged)
        assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==3
        assert 'оригинальную' in answer.call_args.args[0]
    finally:
        await bot.session.close()


async def test_successful_cash_exchange_correction_keeps_each_inventory_snapshot(finance):
    from decimal import Decimal

    from tests.thai.test_trading import common, trade
    await fund(finance,instrument='USDT',quantity=Decimal(1000))
    deal_id=await trade(finance)
    await finance['app'].trading.post(**common(finance,'original'),deal_id=deal_id)
    result=await finance['app'].trading.correct(**common(finance,'correct-cash'),deal_id=deal_id,
        expected_deal_id=deal_id,reason='Изменить сумму',replacement={'mode':'cash',
            'receive':{'instrument':'THB_CASH','quantity':'7000','quoted_rate':'35'},
            'pay':{'instrument':'USDT','quantity':'200','quoted_rate':'1'}})
    assert (await balance(finance,'USDT'))[0]==800
    assert (await balance(finance,'THB_CASH'))[0]==7000
    assert (await finance['app'].trading_queries.detail(result['deal_id']))['effective_status']=='posted'
