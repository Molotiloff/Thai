"""Release acceptance across Telegram, journal, CRM and durable workers."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from app.container import ApplicationContainer
from app.setup_handlers import create_router
from app.thai_telegram import ThaiTelegramCommands, ThaiTelegramDelivery
from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository
from domain.finance.models import InvalidPosting
from services.payment_watch.service import PaymentMonitor
from tests.thai.test_exchange_workflow import wallets
from tests.thai.test_monitoring_bestchange import start, telegram
from tests.thai.test_posting import INSTANT


async def test_removed_commands_cannot_bypass_supported_workflows(finance):
    commands=ThaiTelegramCommands(finance['app'],finance['config'])
    for command in ('/deposit USDT 100','/withdraw USDT 100','/request 1 wallet USDT 1 THB_CASH 35 1 35',
                    '/bc sale 100 35 36 THB_CASH','/profit_close 2026-10-01','/register Other',
                    '/watch 1 recipient our 15','/correct 1 USDT 100 THB_CASH 3500 1 35 reason',
                    '/transfer 100 0 35 THB_BANK physical','/ордер 35','/город екб','/екб','/гар'):
        with pytest.raises(InvalidPosting,match='Неизвестная команда'):
            await commands.execute(telegram_user_id=12345,chat_id=-123,message_id=100,
                text=command,effective_at=INSTANT,chat_name='Must not be created')
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients')==0
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==0
    assert await finance['pool'].fetchval('SELECT count(*) FROM deals')==0


async def test_bot_crm_correction_then_bot_cancel_after_container_restart(finance,monkeypatch):
    from aiogram import Bot
    from aiogram.types import Chat, Message, User

    config=replace(finance['config'],operations_chat_id=-999)
    commands=ThaiTelegramCommands(finance['app'],config)
    bot=Bot('123456:TEST')
    transport=telegram()
    monkeypatch.setattr(bot,'send_message',transport.send_message)
    monkeypatch.setattr(bot,'edit_message_text',transport.edit_message_text)
    monkeypatch.setattr(Message,'answer',AsyncMock())
    monkeypatch.setattr(Message,'reply',AsyncMock())
    user=User(id=12345,is_bot=False,first_name='Manager')
    chat=Chat(id=-123,type='supergroup',title='Клиент')
    message=Message(message_id=1,date=INSTANT,chat=chat,from_user=user,text='/start').as_(bot)
    router=create_router(commands)
    try:
        await router.message.handlers[0].callback(message)
        message=message.model_copy(update={'message_id':2,'text':'/пт 100 обат 3300'}).as_(bot)
        await router.message.handlers[0].callback(message)
        assert (await wallets(finance))['USDT']==-100
        assert transport.send_message.await_count==2
        # Recreate the application as after a worker/backend restart.
        restored=ApplicationContainer.build(finance['pool'])
        app=create_thai_finance_api(container=restored,jwt_secret='x'*32)
        cashier=await finance['pool'].fetchval("INSERT INTO users(display_name,telegram_user_id) VALUES ('Cashier',777) RETURNING id")
        await finance['pool'].execute("INSERT INTO user_roles VALUES ($1,'cashier')",cashier)
        def headers(tg):
            return {'Authorization':'Bearer '+create_access_token(tg_user_id=tg,secret='x'*32,ttl_seconds=60)}
        payload={'idempotency_key':'crm-correct','effective_at':INSTANT.isoformat(),'expected_deal_id':1,
                 'reason':'Исправление в CRM','replacement':{'mode':'wallet',
                 'receive':{'instrument':'USDT','quantity':'120'},'pay':{'instrument':'THB_BANK','quantity':'4200'}}}
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as http:
            assert (await http.get('/api/v1/deals')).status_code==401
            listing=await http.get('/api/v1/deals',headers=headers(777))
            assert listing.status_code==200 and listing.json()['items'][0]['id']==1
            assert (await http.post('/api/v1/deals/1/correction',json=payload,headers=headers(777))).status_code==403
            await finance['pool'].execute("INSERT INTO period_locks(context,period) VALUES ('ledger','2026-10-01')")
            blocked=await http.post('/api/v1/deals/1/correction',json=payload,headers=headers(12345))
            assert blocked.status_code==409 and blocked.json()['error']['code']=='period_closed'
            assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==1
            await finance['pool'].execute("DELETE FROM period_locks WHERE context='ledger'")
            corrected=await http.post('/api/v1/deals/1/correction',json=payload,headers=headers(12345))
            assert corrected.status_code==200,corrected.text
            assert (await http.post('/api/v1/deals/1/correction',json=payload,headers=headers(12345))).json()==corrected.json()
            assert corrected.json()['request_id']==1 and corrected.json()['deal_id']==2
        delivery=ThaiTelegramDelivery(restored,bot,-999)
        dispatcher=restored.outbox_dispatcher(delivery)
        while await dispatcher.run_once():
            pass
        assert transport.send_message.await_count==2 # Same original cards, no new messages.
        assert '4’200.00 безнал' in transport.edit_message_text.call_args.args[0]
        router=create_router(ThaiTelegramCommands(restored,config))
        cancellation=message.model_copy(update={'message_id':3,'text':'/отмена 1 отмена клиента'}).as_(bot)
        await router.message.handlers[0].callback(cancellation)
        while await dispatcher.run_once():
            pass
        assert all(value==0 for value in (await wallets(finance)).values())
        assert all(row['balance']==0 for row in await restored.queries.cash_balances())
        assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==4
        assert transport.send_message.await_count==2
        assert transport.edit_message_text.call_args.kwargs['reply_markup'] is None
    finally:
        await bot.session.close()


async def test_payment_notifications_retry_and_expired_lease_after_worker_restart(finance):
    watch=await start(finance)
    transport=telegram()
    transport.send_message.side_effect=TelegramNetworkError(
        method=SendMessage(chat_id=-123,text='Notice'),message='token=secret must not enter outbox')
    app=finance['app']
    dispatcher=app.outbox_dispatcher(ThaiTelegramDelivery(app,transport))
    # Start/status events have no financial operation; they must still be claimable.
    assert await dispatcher.run_once()
    event=await finance['pool'].fetchrow('SELECT * FROM telegram_outbox')
    assert event['status']=='failed' and event['last_error']=='delivery_failed'
    transport.send_message.side_effect=None
    transport.send_message.return_value=SimpleNamespace(message_id=900)
    await finance['pool'].execute('UPDATE telegram_outbox SET available_at=clock_timestamp()')
    stale=await OutboxDeliveryRepository(finance['pool']).claim()
    assert stale is not None
    # Simulate a process that died after claiming, before committing delivery.
    await finance['pool'].execute("UPDATE telegram_outbox SET lease_until=clock_timestamp()-interval '1 second'")
    restored=ApplicationContainer.build(finance['pool'])
    worker=restored.outbox_dispatcher(ThaiTelegramDelivery(restored,transport))
    assert await worker.run_once()
    assert not await worker.run_once()
    assert (await restored.trading_queries.payment_watch(watch['id']))['notice_message_id']==900
    await OutboxDeliveryRepository(finance['pool']).retry(stale,'stale worker')
    assert await finance['pool'].fetchval('SELECT status FROM telegram_outbox')=='sent'
    await finance['pool'].execute("UPDATE payment_watches SET timeout_at=clock_timestamp()-interval '1 second'")
    gateway=SimpleNamespace(list_usdt_transfers=AsyncMock(return_value=[]))
    await PaymentMonitor(restored.payment_watches,gateway).run_once()
    assert await worker.run_once()
    assert 'Продолжить' in transport.edit_message_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0].text
    assert transport.send_message.await_count==2 # One failed attempt and one successful notice.
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==0
