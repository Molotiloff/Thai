"""Russian auxiliary bot commands use the independent DB and existing archive stack."""

import asyncio
import io
import zipfile
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import asyncpg
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, GetFile, SendDocument, SendPhoto
from aiogram.types import (
    CallbackQuery,
    Chat,
    Document,
    File,
    Message,
    PhotoSize,
    Update,
    User,
    Voice,
)

from app.setup_handlers import create_router, setup_message_archive
from app.thai_telegram import ThaiTelegramCommands
from domain.finance.models import IdempotencyConflict, InvalidPosting, PermissionDenied
from services.xe_api import XEConvertResult
from tests.thai.test_posting import INSTANT


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent=[]
        self.documents=[]
        self.serial=1000

    async def close(self):
        pass

    async def make_request(self,bot,method,timeout=None):
        if isinstance(method,AnswerCallbackQuery):
            self.sent.append(method)
            return True
        if isinstance(method,GetFile):
            return File(file_id=method.file_id,file_unique_id=method.file_id,file_path='file.dat')
        self.sent.append(method)
        self.serial+=1
        chat_id=int(method.chat_id)
        data={'message_id':self.serial,'date':INSTANT,'chat':Chat(id=chat_id,type='private' if chat_id>0 else 'supergroup',title='Чат'),
            'from_user':User(id=bot.id,is_bot=True,first_name='Bot'),'text':getattr(method,'text',None),'caption':getattr(method,'caption',None)}
        if isinstance(method,SendDocument):
            with open(method.document.path,'rb') as stream:
                self.documents.append(stream.read())
            data['document']=Document(file_id='export-file',file_unique_id='export-file',file_name='archive.zip')
        if isinstance(method,SendPhoto):
            data['photo']=[PhotoSize(file_id='sent-photo',file_unique_id='sent-photo',width=10,height=10)]
        return Message(**data).as_(bot)

    async def stream_content(self,*args,**kwargs):
        yield b'test media bytes'


def message(text=None,*,chat=-123,title='Клиент A',user=12345,mid=1,**kwargs):
    return Message(message_id=mid,date=INSTANT,chat=Chat(id=chat,type='private' if chat>0 else 'supergroup',title=title),
        from_user=User(id=user,is_bot=False,first_name='<Manager>',username='manager'),text=text,**kwargs)


async def feed(dispatcher,bot,msg,*,edited=False):
    return await dispatcher.feed_update(bot,Update(update_id=msg.message_id+int(edited)*10000,
        **{'edited_message' if edited else 'message':msg}))


async def callback(dp,bot,*,data,mid,chat=-900,user=12345):
    cq=CallbackQuery(id=f'{chat}:{mid}:{user}:{data}',chat_instance='test',
        from_user=User(id=user,is_bot=False,first_name='Manager'),data=data,
        message=message('Подтверждение',chat=chat,mid=mid,user=bot.id))
    await dp.feed_update(bot,Update(update_id=mid+10000,callback_query=cq))


async def test_whoami_calc_and_xe_restore_original_handlers(finance):
    session=RecordingSession()
    bot=Bot('123456:TEST',session=session)
    commands=ThaiTelegramCommands(finance['app'],finance['config'])
    dp=Dispatcher()
    dp.include_router(create_router(commands))
    commands.xe.converter_service=AsyncMock()
    commands.xe.converter_service.convert_text.return_value=XEConvertResult('USD','THB',Decimal(1000),Decimal(35),
        Decimal(35000),Decimal(34650),Decimal(1),'%',-1,'https://converter.example/image')
    try:
        await feed(dp,bot,message('/whoami@bot',user=999,mid=1))
        text=session.sent[-1].text
        assert '999' in text and '-123' in text and '&lt;Manager&gt;' in text
        await feed(dp,bot,message('/calc@bot (100+20)/3',user=999,mid=2))
        assert '40' in session.sent[-1].text
        await feed(dp,bot,message('/100+20',user=999,mid=3))
        assert '120' in session.sent[-1].text
        await feed(dp,bot,message('/xe@bot USD THB 1k-1%',mid=4))
        commands.xe.converter_service.convert_text.assert_awaited_once_with(text='USD THB 1000-1%',include_image=True)
        assert isinstance(session.sent[-1],SendPhoto)
        assert 'THB' in session.sent[-1].caption and 'xe.com' in session.sent[-1].caption
        await feed(dp,bot,message('/xe USD THB 1',user=999,mid=5))
        assert 'Доступ только' in session.sent[-1].text
        assert commands.xe.converter_service.convert_text.await_count==1
        await finance['pool'].execute('UPDATE users SET is_active=FALSE WHERE id=$1',finance['actor'])
        await feed(dp,bot,message('/xe USD THB 1',mid=6))
        assert commands.xe.converter_service.convert_text.await_count==1
        assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==0
        assert await finance['pool'].fetchval('SELECT count(*) FROM clients')==0
    finally:
        await bot.session.close()


async def test_act_separate_service_account_manual_reconcile_trade_edit_and_reversal(finance):
    app=finance['app']
    config=replace(finance['config'],admin_chat_id=-900)
    commands=ThaiTelegramCommands(app,config)
    session=RecordingSession()
    bot=Bot('123456:TEST',session=session)
    dp=Dispatcher()
    dp.include_router(create_router(commands))
    async def amount():
        return await app.act_counter.get_current_amount(request_chat_id=-900)
    async def reconcile(key,value):
        return await app.act_counter.reconcile(request_chat_id=-900,amount=Decimal(value),
            actor_id=finance['actor'],key=key)
    try:
        await feed(dp,bot,message('/акт',chat=-900,mid=1))
        assert 'USDT ACT' in session.sent[-1].text
        assert await amount()==0
        assert await finance['pool'].fetchval('SELECT count(*) FROM clients')==0
        await feed(dp,bot,message('/акт -10+2',chat=-900,mid=2))
        assert await amount()==-8
        assert 'ACT стало' in session.sent[-1].text
        await feed(dp,bot,message('/акт 500',chat=-900,user=999,mid=3))
        assert 'Доступ только' in session.sent[-1].text
        await feed(dp,bot,message('/акт 500',chat=-123,mid=4))
        assert 'чате заявок' in session.sent[-1].text and await amount()==-8
        assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==0
        for mid,text in ((5,'/start'),(6,'/пт 100 обат 3300')):
            await commands.execute(telegram_user_id=12345,chat_id=-123,message_id=mid,
                text=text,effective_at=INSTANT,chat_name='Клиент')
        assert await amount()==92
        before=await app.client_queries.wallets(1)
        result=await reconcile('manual','1000')
        assert Decimal(result['previous_amount'])==92 and Decimal(result['delta'])==908
        assert await app.client_queries.wallets(1)==before
        assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==1
        common={'key':'edit','actor_id':finance['actor'],'effective_at':INSTANT,'business_date':INSTANT.date()}
        edited=await app.exchange_requests.edit_accounting(text='/пт 120 обезнал 4200',deal_id=1,
            common=common,actor_name='Manager')
        assert await amount()==1020
        assert await reconcile('manual','1000')==result and await amount()==1020
        with pytest.raises(IdempotencyConflict):
            await reconcile('manual','1001')
        await app.exchange_requests.cancel_accounting(deal_id=1,expected_deal_id=edited['deal_id'],
            common={**common,'key':'cancel'},reason='Проверка')
        assert await amount()==900
        assert all(row['balance']==0 for row in await app.queries.cash_balances())
        with pytest.raises(InvalidPosting):
            await reconcile('precision','0.0000001')
        assert await finance['pool'].fetchval('SELECT count(*) FROM act_counter_adjustments')==2
        await asyncio.gather(reconcile('concurrent-a','2000'),reconcile('concurrent-b','3000'))
        assert await amount() in (2000,3000)
        adjustment=await finance['pool'].fetchval('SELECT sum(delta) FROM act_counter_adjustments')
        assert await amount()==adjustment # All exchanges have been reversed.
        async with finance['pool'].acquire() as db,db.transaction():
            with pytest.raises(asyncpg.CheckViolationError):
                await db.execute('DELETE FROM act_counter_adjustments')
        await finance['pool'].execute('UPDATE users SET is_active=FALSE WHERE id=$1',finance['actor'])
        with pytest.raises(PermissionDenied):
            await reconcile('revoked','0')
        await feed(dp,bot,message('/акт 0',chat=-900,mid=8))
        assert 'Доступ только' in session.sent[-1].text
    finally:
        await bot.session.close()


async def test_archive_real_middleware_edits_media_and_admin_export(finance,tmp_path):
    config=replace(finance['config'],admin_chat_id=-900,message_archive_media_dir=str(tmp_path/'media'),
        message_archive_temp_dir=str(tmp_path/'exports'))
    session=RecordingSession()
    bot=Bot('123456:TEST',session=session)
    dp=Dispatcher()
    handler,lifecycle=setup_message_archive(dp,bot,finance['app'],config)
    dp.include_router(create_router(ThaiTelegramCommands(finance['app'],config),archive_handler=handler))
    await lifecycle.start()
    try:
        original=message('Первая версия',user=999)
        await feed(dp,bot,original)
        edited=original.model_copy(update={'text':'<script>Правка</script>','edit_date':int((INSTANT+timedelta(seconds=1)).timestamp())})
        await feed(dp,bot,edited,edited=True)
        await feed(dp,bot,edited,edited=True)
        await feed(dp,bot,message(None,user=999,mid=2,caption='Фото',media_group_id='album',
            photo=[PhotoSize(file_id='photo-file',file_unique_id='photo-file',width=10,height=10)]))
        await feed(dp,bot,message(None,user=999,mid=3,voice=Voice(file_id='voice-file',file_unique_id='voice-file',duration=1)))
        await feed(dp,bot,message('/whoami',user=999,mid=4))
        await lifecycle._media.wait_until_idle()
        assert await finance['pool'].fetchval("SELECT count(*) FROM message_archive_attachments WHERE download_status='ready'")==2
        assert await finance['pool'].fetchval('SELECT revision_no FROM message_archive_messages WHERE telegram_message_id=1')==2
        assert await finance['pool'].fetchval("SELECT count(*) FROM message_archive_messages WHERE direction='outbound'")==1
        # Current client name may differ from the Telegram title; lookup uses Thai chat links.
        await finance['app'].clients.create_client(key='client',actor_id=finance['actor'],name='Другое имя',chat_id=-123)
        assert len(await finance['app'].message_archive.search_archive_chats('Другое имя'))==1
        await feed(dp,bot,message('/сообщение Другое имя',chat=-900,title='Админ',user=999,mid=20))
        assert not handler._tasks and not session.documents
        assert 'действующим сотрудникам' in session.sent[-1].text
        await feed(dp,bot,message('/сообщение Другое имя',chat=-123,user=12345,mid=21))
        assert 'только в админском' in session.sent[-1].text
        await feed(dp,bot,message('/сообщение@bot Другое имя',chat=-900,title='Админ',mid=22))
        await asyncio.gather(*tuple(handler._tasks))
        assert len(session.documents)==1
        with zipfile.ZipFile(io.BytesIO(session.documents[0])) as archive:
            html=archive.read(next(name for name in archive.namelist() if name.endswith('.html'))).decode()
            assert '&lt;script&gt;Правка&lt;/script&gt;' in html
            assert '19:00' in html # 12:00 UTC -> Bangkok.
            media=[name for name in archive.namelist() if name.startswith('media/') and not name.endswith('/')]
            assert len(media)==2
        assert await finance['pool'].fetchval("SELECT status FROM message_archive_exports")=='completed'
        assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==0
        assert not list((tmp_path/'exports').iterdir())
        # A queued export rechecks staff access before generating or sending data.
        await finance['pool'].execute('UPDATE users SET is_active=FALSE WHERE id=$1',finance['actor'])
        archive_id=await finance['pool'].fetchval('SELECT id FROM message_archive_chats WHERE telegram_chat_id=-123')
        assert handler._start_export(archive_chat_id=archive_id,requested_by_user_id=12345,requested_in_chat_id=-900)
        await asyncio.gather(*tuple(handler._tasks))
        assert len(session.documents)==1
        assert await finance['pool'].fetchval("SELECT count(*) FROM message_archive_exports WHERE status='failed'")==1
    finally:
        await lifecycle.stop()
        await bot.session.close()


async def test_manager_command_audit_roles_retry_and_revocation(finance):
    app=finance['app']
    commands=ThaiTelegramCommands(app,replace(finance['config'],admin_chat_id=-900))
    session=RecordingSession()
    bot=Bot('123456:TEST',session=session)
    dp=Dispatcher()
    dp.include_router(create_router(commands))
    try:
        await feed(dp,bot,message('/mgr@bot + 777 <Staff>',chat=-900,mid=1))
        assert await app.client_queries.is_manager(777)
        assert '&lt;Staff&gt;' in session.sent[-1].text
        await feed(dp,bot,message('/mgr + 777 <Staff>',chat=-900,mid=1))
        assert await finance['pool'].fetchval("SELECT count(*) FROM client_directory_events WHERE kind='manager_change'")==1
        await feed(dp,bot,message('/mgr + 888 Evil',chat=-900,user=777,mid=2))
        assert await app.client_queries.is_manager(888)
        await feed(dp,bot,message('/mgr',chat=-900,mid=3))
        assert '777' in session.sent[-1].text
        await feed(dp,bot,message('/mgr - 777',chat=-123,mid=4))
        assert await app.client_queries.is_manager(777)
        await feed(dp,bot,message('/mgr - 777',chat=-900,mid=5))
        assert not await app.client_queries.is_manager(777)
        # Other roles of an existing owner survive removal of manager role.
        await feed(dp,bot,message('/mgr - 12345',chat=-900,mid=6))
        assert await app.client_queries.is_manager(12345)
        await finance['pool'].execute('UPDATE users SET is_active=FALSE WHERE telegram_user_id=777')
        await feed(dp,bot,message('/mgr + 777 Again',chat=-900,mid=7))
        assert 'отключён' in session.sent[-1].text
        assert not await app.client_queries.is_manager(777)
        assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==0
    finally:
        await bot.session.close()


async def test_broadcast_original_reply_preview_confirmation_and_role_checks(finance):
    app=finance['app']
    config=replace(finance['config'],admin_chat_id=-900,operations_chat_id=-901)
    commands=ThaiTelegramCommands(app,config)
    for chat,group in ((-123,'balance'),(-124,'partner'),(-900,'balance'),(-901,'balance')):
        await app.clients.create_client(key=f'client:{chat}',actor_id=finance['actor'],name=str(chat),chat_id=chat,group_code=group)
    session=RecordingSession()
    bot=Bot('123456:TEST',session=session)
    dp=Dispatcher()
    dp.include_router(create_router(commands))
    try:
        await feed(dp,bot,message('/всем',chat=-900,user=999,mid=1))
        assert 'Доступ только' in session.sent[-1].text
        await feed(dp,bot,message('/всем Баланс',chat=-900,mid=2))
        prompt=message('Прикрепите',chat=-900,mid=session.serial,user=bot.id)
        # A reply beginning with slash is previewed rather than executed as a command.
        await feed(dp,bot,message('/текст рассылки',chat=-900,mid=3,reply_to_message=prompt))
        control=session.serial
        assert session.sent[-1].text=='/текст рассылки'
        assert commands.broadcast.session_store.get_payload(control_message_id=control) is not None
        await callback(dp,bot,data='broadcast:confirm',mid=control,user=999)
        assert commands.broadcast.session_store.get_payload(control_message_id=control) is not None
        await callback(dp,bot,data='broadcast:confirm',mid=control,chat=-123)
        assert commands.broadcast.session_store.get_payload(control_message_id=control) is not None
        await callback(dp,bot,data='broadcast:confirm',mid=control)
        sent=[m for m in session.sent if getattr(m,'text',None)=='/текст рассылки' and getattr(m,'chat_id',None)==-123]
        assert len(sent)==1
        assert not any(getattr(m,'text',None)=='/текст рассылки' and getattr(m,'chat_id',None) in (-124,-901) for m in session.sent)
        await callback(dp,bot,data='broadcast:confirm',mid=control)
        assert len([m for m in session.sent if getattr(m,'text',None)=='/текст рассылки' and getattr(m,'chat_id',None)==-123])==1
        await feed(dp,bot,message('/всем',chat=-900,mid=4))
        prompt=message('Прикрепите',chat=-900,mid=session.serial,user=bot.id)
        await feed(dp,bot,message(None,chat=-900,mid=5,reply_to_message=prompt,caption='Фото',
            photo=[PhotoSize(file_id='picture',file_unique_id='picture',width=10,height=10)]))
        control=session.serial
        assert isinstance(session.sent[-1],SendPhoto)
        await finance['pool'].execute('UPDATE users SET is_active=FALSE WHERE id=$1',finance['actor'])
        await callback(dp,bot,data='broadcast:confirm',mid=control)
        assert not any(isinstance(m,SendPhoto) and m.chat_id in (-123,-124) for m in session.sent)
        assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==0
    finally:
        await bot.session.close()


async def test_aml_existing_queue_and_handler_with_mock_checker(finance,tmp_path):
    from app.setup_handlers import setup_aml
    from services.aml.getblock_settings import GetBlockSettings
    config=replace(finance['config'],getblock=GetBlockSettings('identity','private','en','1','TRX','9','2','2','1','0',str(tmp_path)))
    handler,queue=setup_aml(finance['app'],config)
    checker=AsyncMock()
    checker.check_wallet.return_value={'message_text':'AML report: low risk'}
    queue._checker=checker
    session=RecordingSession()
    bot=Bot('123456:TEST',session=session)
    dp=Dispatcher()
    dp.include_router(create_router(ThaiTelegramCommands(finance['app'],config),aml_handler=handler))
    await queue.start()
    try:
        address='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
        await feed(dp,bot,message('/амл '+address,user=999,mid=1))
        checker.check_wallet.assert_not_awaited()
        await feed(dp,bot,message('/амл nonsense',mid=2))
        checker.check_wallet.assert_not_awaited()
        await feed(dp,bot,message('/амл@bot '+address,mid=3))
        await asyncio.wait_for(queue._queue.join(),timeout=2)
        checker.check_wallet.assert_awaited_once()
        assert any(getattr(m,'text',None)=='AML report: low risk' for m in session.sent)
        await finance['pool'].execute('UPDATE users SET is_active=FALSE WHERE id=$1',finance['actor'])
        await feed(dp,bot,message('/амл '+address,mid=4))
        assert checker.check_wallet.await_count==1
    finally:
        await queue.stop()
        await bot.session.close()


async def test_existing_bot_app_run_starts_and_cleans_up_setup_handlers(finance,tmp_path,monkeypatch):
    from types import SimpleNamespace

    import app.setup_handlers
    import bot_app
    config=replace(finance['config'],bot_token='123456:TEST',api_enabled=False,
        message_archive_media_dir=str(tmp_path/'media'),message_archive_temp_dir=str(tmp_path/'exports'))
    gateway=SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(app.setup_handlers,'TronscanGateway',lambda **kwargs:gateway)
    monkeypatch.setattr(bot_app,'create_pool',AsyncMock(return_value=finance['pool']))
    close=AsyncMock()
    monkeypatch.setattr(bot_app,'close_pool',close)
    application=bot_app.BotApp(config)
    async def poll(bot,**kwargs):
        assert not kwargs['close_bot_session']
        assert 'edited_message' in kwargs['allowed_updates']
        await application._on_startup()
        assert application.services._supervisor.running
        raise RuntimeError('Test polling stopped')
    monkeypatch.setattr(application.dp,'start_polling',poll)
    with pytest.raises(RuntimeError,match='Test polling stopped'):
        await application.run()
    assert application.services._supervisor is None
    assert not application.services.tg_outbox_worker.running
    assert not application.services.payment_watch_poller.running
    assert application.services.message_archive._running is False
    gateway.aclose.assert_awaited_once()
    close.assert_awaited_once_with(finance['pool'])
