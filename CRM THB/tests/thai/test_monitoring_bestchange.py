import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.thai_telegram import ThaiTelegramCommands, ThaiTelegramDelivery
from domain.finance.models import InvalidPosting, PermissionDenied, ReversalBlocked
from services.payment_watch.models import StartPaymentWatchCommand, TronTransfer
from services.payment_watch.service import PaymentMonitor
from tests.thai.test_expenses import common
from tests.thai.test_posting import INSTANT, balance, fund
from tests.thai.test_profit import configure

ADDRESS='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
OUR='TVjsyZ7fYF3qLF6BQgPmTEZy1xrNNyVAAA'


async def start(finance,*,key='watch',test=False,amount='100.123456'):
    if not await finance['app'].trading_queries.client_by_chat(-123):
        await finance['app'].clients.create_client(key='client',actor_id=finance['actor'],name='Client',chat_id=-123)
    command=StartPaymentWatchCommand(chat_id=-123,chat_name='Client',reply_message_id=10,
        reply_text=f'Кошелёк {ADDRESS}',reply_caption=None,created_by_user_id=12345,
        test_mode=test,manager_note=amount,command_message_id=20)
    watch=await finance['app'].payment_watches.start_accounting(command,key=key,actor_id=finance['actor'],our_address=OUR)
    return await finance['app'].trading_queries.payment_watch(watch['watch_id'])


def transfer(watch,*,tx='hash',amount='100.123456',seconds=1):
    return TronTransfer(tx_hash=tx,from_address=OUR,to_address=ADDRESS,amount=Decimal(amount),
        token_symbol='USDT',block_number=100,block_ts=watch['started_at']+timedelta(seconds=seconds),confirmations=2,confirmed=True)


def telegram():
    serial=900
    async def sent(*args,**kwargs):
        nonlocal serial
        serial+=1
        return SimpleNamespace(message_id=serial)
    return SimpleNamespace(send_message=AsyncMock(side_effect=sent),send_photo=AsyncMock(side_effect=sent),edit_message_text=AsyncMock())


async def test_otpr_proof_atomic_cash_wallet_and_duplicate_delivery(finance):
    await fund(finance,instrument='USDT',quantity=Decimal('1000'))
    watch=await start(finance)
    assert (await start(finance))['id']==watch['id']
    assert await finance['pool'].fetchval('SELECT count(*) FROM payment_watch_events')==0
    with pytest.raises(InvalidPosting):
        await finance['app'].wallets.withdraw(**common(finance,'fake-proof'),client_id=watch['client_id'],
            instrument='USDT',quantity='100.123456',payment_watch_id=watch['id'])
    assert (await balance(finance,'USDT'))[0]==1000
    proof=transfer(watch)
    gateway=SimpleNamespace(list_usdt_transfers=AsyncMock(return_value=[replace(proof,tx_hash='low',confirmations=1),
        replace(proof,tx_hash='unconfirmed',confirmed=False),replace(proof,tx_hash='other',from_address=ADDRESS),
        replace(proof,tx_hash='token',token_symbol='FAKE'),replace(proof,tx_hash='old',block_ts=watch['started_at']-timedelta(seconds=1))]))
    monitor=PaymentMonitor(finance['app'].payment_watches,gateway)
    await monitor.run_once()
    assert await finance['pool'].fetchval('SELECT count(*) FROM payment_watch_events')==0
    results=await asyncio.gather(*(finance['app'].payment_watches.settle_accounting(watch_id=watch['id'],transfer=proof) for _ in range(3)))
    assert sum(result is not None for result in results)==1
    assert (await balance(finance,'USDT'))[0]==Decimal('899.876544')
    wallet=next(r for r in await finance['app'].client_queries.wallets(watch['client_id']) if r['instrument_code']=='USDT')
    assert wallet['balance']==-Decimal('100.123456')
    current=await finance['app'].trading_queries.payment_watch(watch['id'])
    assert current['status']=='COMPLETED' and not current['review_required']
    with pytest.raises(ReversalBlocked,match='фактического возврата'):
        await finance['app'].reversals.reverse(**common(finance,'reverse-chain'),original_id=current['events'][0]['operation_id'],reason='Test')
    bot=telegram()
    delivery=ThaiTelegramDelivery(finance['app'],bot)
    await delivery.deliver_payment_watch(watch['id'])
    await delivery.deliver_payment_watch(watch['id'])
    assert bot.send_photo.await_count==1
    assert bot.send_message.await_count==2 # notice and wallet balance
    assert 'Запомнил.' in bot.send_message.call_args.args[1]


async def test_otpr_test_then_main_counts_both_actuals_and_ignores_earlier_main(finance):
    await fund(finance,instrument='USDT',quantity=Decimal('1000'))
    watch=await start(finance,test=True,amount='100')
    main_before=transfer(watch,tx='before',amount='100',seconds=1)
    test=transfer(watch,tx='test',amount='1',seconds=2)
    gateway=SimpleNamespace(list_usdt_transfers=AsyncMock(return_value=[main_before,test]))
    monitor=PaymentMonitor(finance['app'].payment_watches,gateway)
    await monitor.run_once()
    await monitor.run_once()
    assert (await balance(finance,'USDT'))[0]==999
    current=await finance['app'].trading_queries.payment_watch(watch['id'])
    assert current['phase']=='MAIN' and current['status']=='WATCHING'
    gateway.list_usdt_transfers.return_value=[test,main_before,transfer(watch,tx='main',amount='100',seconds=3)]
    await monitor.run_once()
    assert (await balance(finance,'USDT'))[0]==899
    current=await finance['app'].trading_queries.payment_watch(watch['id'])
    assert current['status']=='COMPLETED' and len(current['events'])==2


async def test_otpr_timeout_continue_stop_permissions_and_no_manual_completion(finance):
    watch=await start(finance)
    await finance['pool'].execute('UPDATE payment_watches SET timeout_at=clock_timestamp()-interval \'1 second\' WHERE id=$1',watch['id'])
    gateway=SimpleNamespace(list_usdt_transfers=AsyncMock(return_value=[]))
    monitor=PaymentMonitor(finance['app'].payment_watches,gateway)
    await monitor.run_once()
    assert not gateway.list_usdt_transfers.called
    assert (await finance['app'].trading_queries.payment_watch(watch['id']))['status']=='TIMED_OUT'
    args={'key':'continue','actor_id':finance['actor'],'watch_id':watch['id'],'chat_id':-123,
          'action':'continue','expected_continue_count':0}
    await finance['app'].payment_watches.control_accounting(**args)
    await finance['app'].payment_watches.control_accounting(**args)
    with pytest.raises(InvalidPosting,match='устарела'):
        await finance['app'].payment_watches.control_accounting(**{**args,'key':'stale','action':'stop'})
    with pytest.raises(InvalidPosting,match='чату'):
        await finance['app'].payment_watches.control_accounting(**{**args,'key':'wrong-chat','chat_id':-124,'action':'stop','expected_continue_count':1})
    reader=await finance['pool'].fetchval("INSERT INTO users(display_name) VALUES ('Reader') RETURNING id")
    with pytest.raises(PermissionDenied):
        await finance['app'].payment_watches.control_accounting(**{**args,'key':'reader','actor_id':reader})
    await finance['app'].payment_watches.control_accounting(**{**args,'key':'stop','action':'stop','expected_continue_count':1})
    with pytest.raises(InvalidPosting,match='не активно'):
        await finance['app'].payment_watches.settle_accounting(watch_id=watch['id'],transfer=transfer(watch))
    assert await finance['pool'].fetchval('SELECT count(*) FROM payment_watch_events')==0


async def test_otpr_insufficient_cash_retries_same_fact_and_reports_actual_mismatch(finance):
    watch=await start(finance)
    proof=transfer(watch,amount='99.123456')
    with pytest.raises(InvalidPosting):
        # InsufficientCash is FinanceError; the specific exception is asserted below via state.
        await finance['app'].payment_watches.settle_accounting(watch_id=watch['id'],transfer=replace(proof,amount=Decimal('0')))
    from domain.finance.models import InsufficientCash
    with pytest.raises(InsufficientCash):
        await finance['app'].payment_watches.settle_accounting(watch_id=watch['id'],transfer=proof)
    assert await finance['pool'].fetchval('SELECT count(*) FROM payment_watch_events')==0
    await fund(finance,instrument='USDT',quantity=Decimal('1000'))
    await finance['app'].payment_watches.settle_accounting(watch_id=watch['id'],transfer=proof)
    current=await finance['app'].trading_queries.payment_watch(watch['id'])
    assert current['status']=='COMPLETED' and current['review_required']
    assert (await balance(finance,'USDT'))[0]==Decimal('900.876544')
    assert 'требует проверки' in finance['app'].payment_watches.builder.build_accounting(current)


async def test_best_change_correction_is_atomic_reuses_card_and_close_transfers_daniyar(finance,monkeypatch):
    from tests.thai.test_profit import close
    await configure(finance)
    await fund(finance,instrument='USDT',quantity=Decimal('1000'))
    commands=ThaiTelegramCommands(finance['app'],finance['config'])
    async def send(text,mid):
        return await commands.execute(telegram_user_id=12345,chat_id=-123,message_id=mid,text=text,effective_at=INSTANT)
    text=await send('/бест продажа 100 35 36 бат',10)
    assert 'Валовый спред' in text and 'Доля Данияра' in text and 'RUB' not in text
    deal=await finance['app'].profit_queries.best_change_by_reference('tg:-123:10')
    before=await balance(finance,'USDT')
    original=finance['app'].best_change.record
    monkeypatch.setattr(finance['app'].best_change,'record',AsyncMock(side_effect=InvalidPosting('replacement failed')))
    args={**common(finance,'correction'),'deal_id':deal,'expected_deal_id':deal,'reason':'Test',
          'replacement':{'operation':'sale','quantity':'100','market_rate':'35','client_rate':'37','thb_instrument':'THB_CASH'}}
    with pytest.raises(InvalidPosting,match='replacement failed'):
        await finance['app'].best_change.correct(**args)
    assert await balance(finance,'USDT')==before
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind='reversal'")==0
    monkeypatch.setattr(finance['app'].best_change,'record',original)
    bot=telegram()
    delivery=ThaiTelegramDelivery(finance['app'],bot)
    await delivery.deliver_best_change(deal)
    corrected=await finance['app'].best_change.correct(**args)
    assert await finance['app'].best_change.correct(**args)==corrected
    await delivery.deliver_best_change(deal)
    assert bot.send_message.await_count==1
    assert 'Курс клиента: 37' in bot.edit_message_text.call_args.args[0]
    report=await finance['app'].profit_queries.best_change_report(INSTANT.date().replace(day=1))
    assert report['count']==1
    amount=report['daniyar_usdt']
    await close(finance)
    report=await finance['app'].profit_queries.best_change_report(INSTANT.date().replace(day=1))
    assert sum(row['amount_usdt'] for row in report['transfers'])==amount
    assert report['buffer']['balance']==0


async def test_otpr_and_bestchange_router_restore_original_commands_and_callbacks(finance,monkeypatch):
    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, User

    from app.setup_handlers import create_router
    from tests.thai.test_profit import configure
    from tests.thai.test_russian_telegram import bot as command_bot
    _,send=command_bot(finance)
    await send('/start',chat_name='Клиент')
    await send(f'/setwallet {OUR}',message_id=2)
    assert await send('/кош')==OUR
    commands=ThaiTelegramCommands(finance['app'],finance['config'])
    bot=Bot('123456:TEST')
    transport=telegram()
    answer=AsyncMock()
    callback_answer=AsyncMock()
    monkeypatch.setattr(bot,'send_message',transport.send_message)
    monkeypatch.setattr(bot,'send_photo',transport.send_photo)
    monkeypatch.setattr(bot,'edit_message_text',transport.edit_message_text)
    monkeypatch.setattr(Message,'answer',answer)
    monkeypatch.setattr(CallbackQuery,'answer',callback_answer)
    user=User(id=12345,is_bot=False,first_name='Manager')
    chat=Chat(id=-123,type='supergroup',title='Клиент')
    address=Message(message_id=99,date=INSTANT,chat=chat,from_user=user,caption=f'Кошелёк {ADDRESS}')
    message=Message(message_id=100,date=INSTANT,chat=chat,from_user=user,text='/отпр тест 100',reply_to_message=address).as_(bot)
    router=create_router(commands)
    try:
        await router.message.handlers[0].callback(message)
        assert 'тестовая 1 USDT' in transport.send_message.call_args.args[1]
        assert '2 confirmations' in transport.send_message.call_args.args[1]
        data=transport.send_message.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
        watch_id=int(data.split(':')[2])
        card=Message(message_id=901,date=INSTANT,chat=chat).as_(bot)
        callback=CallbackQuery(id='stop',from_user=user,message=card,chat_instance='x',data=data).as_(bot)
        handler=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='control')
        forged=callback.model_copy(update={'message':card.model_copy(update={'message_id':902})}).as_(bot)
        await handler(forged)
        assert callback_answer.call_args.kwargs['show_alert']
        await handler(callback)
        assert (await finance['app'].trading_queries.payment_watch(watch_id))['status']=='STOPPED'
        buffer,_=await configure(finance)
        await fund(finance,instrument='USDT',quantity=Decimal('1000'))
        bc=message.model_copy(update={'message_id':101,'text':'/бест продажа 100 35 36 безнал','reply_to_message':None}).as_(bot)
        await router.message.handlers[0].callback(bc)
        assert 'THB безнал' in transport.send_message.call_args.args[1]
        assert 'Доля Данияра' in transport.send_message.call_args.args[1]
        await finance['app'].clients.bind_chat(key='bind-buffer',actor_id=finance['actor'],client_id=buffer,chat_id=-124)
        buffer_chat=Chat(id=-124,type='supergroup',title='BestChange')
        for index,command in enumerate(('/кошелек','/кошелёк','/дай'),start=102):
            wallet=message.model_copy(update={'message_id':index,'chat':buffer_chat,'text':command,'reply_to_message':None}).as_(bot)
            await router.message.handlers[0].callback(wallet)
            assert 'Доля Данияра в буфере: 1' in answer.call_args.args[0]
            assert 'Остаток CoinDrop' in answer.call_args.args[0]
    finally:
        await bot.session.close()


async def test_crm_bestchange_correction_and_watch_read_auth(finance):
    from httpx import ASGITransport, AsyncClient

    from api.auth import create_access_token
    from api.thai_finance import create_thai_finance_api
    await configure(finance)
    await fund(finance,instrument='USDT',quantity=Decimal('1000'))
    posted=await finance['app'].best_change.record(**common(finance,'bc'),operation='sale',quantity='100',market_rate='35',client_rate='36')
    deal=await finance['pool'].fetchval('SELECT id FROM deals WHERE operation_id=$1',posted.id)
    watch=await start(finance)
    api=create_thai_finance_api(container=finance['app'],jwt_secret='x'*32)
    token=create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as http:
        assert (await http.get(f"/api/v1/payment-watches/{watch['id']}")).status_code==401
        http.headers['Authorization']='Bearer '+token
        assert (await http.get(f"/api/v1/payment-watches/{watch['id']}")).json()['status']=='WATCHING'
        assert (await http.post(f'/api/v1/deals/{deal}/settlement',json={})).status_code==404
        result=await http.post(f'/api/v1/best-change/deals/{deal}/correct',json={
            'idempotency_key':'api-correct','effective_at':INSTANT.isoformat(),'expected_deal_id':deal,
            'operation':'sale','quantity':'100','market_rate':'35','client_rate':'37','reason':'Исправление'})
        assert result.status_code==200,result.text
        stale=await http.post(f'/api/v1/best-change/deals/{deal}/correct',json={
            'idempotency_key':'api-stale','effective_at':INSTANT.isoformat(),'expected_deal_id':deal,
            'operation':'sale','quantity':'100','market_rate':'35','client_rate':'38','reason':'Устаревшее'})
        assert stale.status_code==422,stale.text
