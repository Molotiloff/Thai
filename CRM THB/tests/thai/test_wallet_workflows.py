from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZipFile

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from app.setup_handlers import create_router
from app.thai_telegram import ThaiTelegramCommands, ThaiTelegramDelivery
from domain.finance.models import InvalidPosting, PeriodClosed, PermissionDenied, ReversalBlocked
from services.finance.outbox import OutboxEvent
from telegram_adapters.statements import _build_statement_xlsx, _month_bounds
from tests.thai.test_expenses import common
from tests.thai.test_posting import INSTANT

RATES = {'THB_CASH':'35','THB_BANK':'36','USDT':'1','USD_WH':'1.01','USD_BL':'1','EUR':'1.1','EUR500':'1.2','RUB':'0.01'}


async def client(finance):
    return (await finance['app'].clients.create_client(key='client',actor_id=finance['actor'],
        name='Клиент <Тест>',chat_id=-123))['client_id']


async def adjust(finance,cid,key,quantity='10',instrument='USDT',**options):
    return await finance['app'].wallet_interactions.accounting_adjust(**{**common(finance,key),**options},
        client_id=cid,instrument=instrument,quantity=quantity,quoted_rate=RATES[instrument])


async def balance(finance,cid,code='USDT'):
    return next(r for r in await finance['app'].client_queries.wallets(cid) if r['instrument_code']==code)


def telegram():
    counter=900
    async def sent(*args,**kwargs):
        nonlocal counter
        counter+=1
        return SimpleNamespace(message_id=counter)
    return SimpleNamespace(send_message=AsyncMock(side_effect=sent),edit_message_text=AsyncMock())


async def test_statement_month_boundaries_opening_balances_and_eight_instruments(finance):
    cid=await client(finance)
    before=datetime(2026,9,30,16,59,tzinfo=UTC)
    start=datetime(2026,9,30,17,tzinfo=UTC)
    end=datetime(2026,10,31,17,tzinfo=UTC)
    assert _month_bounds(datetime(2026,10,1,0,tzinfo=UTC),timezone='Asia/Bangkok')==(start,end)
    await adjust(finance,cid,'opening','350','THB_CASH',effective_at=before,business_date=before.date())
    operation=await adjust(finance,cid,'month-start','35','THB_CASH',effective_at=start,business_date=start.date(),comment='=SUM(1,2)')
    await adjust(finance,cid,'next-month','70','THB_CASH',effective_at=end,business_date=end.date())
    for code in RATES:
        if code!='THB_CASH':
            await adjust(finance,cid,'currency:'+code,'-1.123456' if code=='USDT' else '-1.12',code)
    rows=await finance['app'].wallet_interactions.export_transactions(client_id=cid,since=start,until=end)
    assert {r['currency_code'] for r in rows}==set(RATES)
    thb=next(r for r in rows if r['currency_code']=='THB_CASH')
    assert thb['operation_id']==operation.id and thb['amount']==35 and thb['balance_after']==385
    blob=_build_statement_xlsx(rows,'=Клиент','Октябрь',timezone='Asia/Bangkok')
    with ZipFile(BytesIO(blob)) as archive:
        strings=archive.read('xl/sharedStrings.xml').decode()
        sheet=archive.read('xl/worksheets/sheet1.xml').decode()
        assert 'Asia/Bangkok' in strings and '=SUM(1,2)' in strings
        assert '<f>' not in sheet
        assert '-1.123456' in sheet


async def test_statement_pages_cover_history_without_losing_balance(finance):
    cid=await client(finance)
    operations=[await adjust(finance,cid,f'row:{i}','1.123456') for i in range(35)]
    pages=[]
    cursor=None
    while True:
        page=await finance['app'].wallet_interactions.statement_page(client_id=cid,instrument='USDT',before_id=cursor)
        pages.append(page)
        cursor=page['next_before_id']
        if cursor is None:
            break
    assert [len(p['rows']) for p in pages]==[15,15,5]
    assert [r['id'] for p in pages for r in p['rows']]==[o.id for o in reversed(operations)]
    assert pages[0]['rows'][0]['balance_after']==Decimal('39.320960')
    assert len(await finance['app'].wallet_interactions.export_transactions(client_id=cid))==35


async def test_manual_undo_all_instruments_uses_links_and_updates_original_card(finance):
    cid=await client(finance)
    bot=telegram()
    delivery=ThaiTelegramDelivery(finance['app'],bot)
    for code in RATES:
        op=await adjust(finance,cid,'adjust:'+code,'-10.123456' if code=='USDT' else '-10.12',code)
        await delivery.deliver_wallet_adjustment(op.id)
        message_id=await finance['pool'].fetchval(
            "SELECT message_id FROM telegram_message_links WHERE operation_id=$1",op.id)
        args={**common(finance,'unused'),'chat_id':-123,'message_id':message_id}
        args.pop('key')
        result=await finance['app'].wallet_interactions.accounting_undo(**args)
        assert await finance['app'].wallet_interactions.accounting_undo(**args)==result
        assert (await balance(finance,cid,code))['balance']==0
        await delivery.deliver(OutboxEvent(1,'old','operation.posted',f'{{"operation_id":{op.id}}}','lease'))
        assert bot.edit_message_text.call_args.kwargs['reply_markup'] is None
        assert 'Отменено' in bot.edit_message_text.call_args.args[0]
    assert bot.send_message.await_count==8
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind='reversal'")==8
    assert all(r['balance']==0 for r in await finance['app'].queries.cash_balances())


async def test_undo_rejects_forged_chat_role_closed_month_and_dependent_movements(finance):
    cid=await client(finance)
    op=await adjust(finance,cid,'first')
    bot=telegram()
    await ThaiTelegramDelivery(finance['app'],bot).deliver_wallet_adjustment(op.id)
    args={**common(finance,'undo'),'chat_id':-123,'message_id':901}
    args.pop('key')
    with pytest.raises(InvalidPosting,match='не найдена'):
        await finance['app'].wallet_interactions.accounting_undo(**{**args,'chat_id':-124})
    reader=await finance['pool'].fetchval("INSERT INTO users(display_name) VALUES ('Reader') RETURNING id")
    with pytest.raises(PermissionDenied):
        await finance['app'].wallet_interactions.accounting_undo(**{**args,'actor_id':reader})
    await finance['pool'].execute("INSERT INTO period_locks(context,period) VALUES ('ledger','2026-10-01')")
    with pytest.raises(PeriodClosed):
        await finance['app'].wallet_interactions.accounting_undo(**args)
    await finance['pool'].execute('DELETE FROM period_locks')
    await adjust(finance,cid,'second')
    with pytest.raises(ReversalBlocked,match='последующие'):
        await finance['app'].wallet_interactions.accounting_undo(**args)
    assert (await balance(finance,cid))['balance']==20


async def test_zero_writes_one_operation_closes_client_and_preserves_cash_profit(finance):
    cid=await client(finance)
    for i,code in enumerate(RATES):
        await adjust(finance,cid,'seed:'+code,'100' if i%2 else '-100',code)
    cash_before=await finance['app'].queries.cash_balances()
    report_before=await finance['app'].profit_queries.report(INSTANT.date().replace(day=1))
    preview=await finance['app'].wallet_interactions.prepare_zero(key='zero',actor_id=finance['actor'],client_id=cid)
    assert len(preview['wallets'])==8
    args={**common(finance,'zero'),'confirmed':True}
    result=await finance['app'].wallet_interactions.resolve_zero(**args)
    assert await finance['app'].wallet_interactions.resolve_zero(**args)==result
    assert all(r['balance']==r['carrying_usdt']==0 for r in await finance['app'].client_queries.wallets(cid))
    assert not await finance['pool'].fetchval('SELECT is_active FROM clients WHERE id=$1',cid)
    assert await finance['pool'].fetchval('SELECT count(*) FROM ledger_accounts a JOIN client_wallet_accounts w ON w.ledger_account_id=a.id WHERE w.client_id=$1 AND a.is_active',cid)==0
    assert await finance['app'].trading_queries.client_by_chat(-123) is None
    assert await finance['app'].queries.cash_balances()==cash_before
    assert await finance['app'].profit_queries.report(INSTANT.date().replace(day=1))==report_before
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE command_details->>'purpose'='client_zero'")==1
    from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository
    bot=telegram()
    delivery=ThaiTelegramDelivery(finance['app'],bot)
    repository=OutboxDeliveryRepository(finance['pool'])
    while event := await repository.claim():
        await delivery.deliver(event)
        await repository.delivered(event)
    assert 'Клиент обнулён и закрыт' in bot.send_message.call_args.args[1]
    with pytest.raises(ReversalBlocked,match='закрытием'):
        await finance['app'].reversals.reverse(**common(finance,'undo-zero'),original_id=result['operation_id'],reason='Test')
    with pytest.raises(InvalidPosting,match='закрыт'):
        await adjust(finance,cid,'after-close')


async def test_zero_rejects_stale_history_wrong_initiator_and_rolls_back_close_failure(finance,monkeypatch):
    from db_asyncpg.thai_clients.repository import ClientsRepository
    cid=await client(finance)
    await adjust(finance,cid,'seed')
    await finance['app'].wallet_interactions.prepare_zero(key='stale',actor_id=finance['actor'],client_id=cid)
    move=await adjust(finance,cid,'extra','1')
    await finance['app'].reversals.reverse(**common(finance,'reverse-extra'),original_id=move.id,reason='Test')
    with pytest.raises(InvalidPosting,match='история изменились'):
        await finance['app'].wallet_interactions.resolve_zero(**common(finance,'stale'),confirmed=True)
    assert (await balance(finance,cid))['balance']==10
    await finance['app'].wallet_interactions.prepare_zero(key='fresh',actor_id=finance['actor'],client_id=cid)
    with pytest.raises(InvalidPosting,match='создавший'):
        await finance['app'].wallet_interactions.resolve_zero(**{**common(finance,'fresh'),'actor_id':999},confirmed=True)
    original=ClientsRepository.close_client
    monkeypatch.setattr(ClientsRepository,'close_client',AsyncMock(side_effect=InvalidPosting('close failed')))
    with pytest.raises(InvalidPosting,match='close failed'):
        await finance['app'].wallet_interactions.resolve_zero(**common(finance,'fresh'),confirmed=True)
    assert (await balance(finance,cid))['balance']==10
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE command_details->>'purpose'='client_zero'")==0
    monkeypatch.setattr(ClientsRepository,'close_client',original)
    assert not (await finance['app'].wallet_interactions.resolve_zero(**common(finance,'fresh'),confirmed=True))['cancelled']


async def test_group_commands_use_same_wallet_for_loans_and_filter_closed_clients(finance):
    cid=await client(finance)
    await adjust(finance,cid,'balance','-100','THB_BANK')
    before=await finance['app'].client_queries.wallets(cid)
    groups=finance['app'].client_groups
    await groups.assign_accounting(text='/группа -123 Партнёр',key='partner',actor_id=finance['actor'],chat_id=-999)
    loans=(await finance['app'].loans.balances())['items']
    assert len(loans)==1 and loans[0]['instrument_code']=='THB_BANK' and loans[0]['balance']==-100
    assert await finance['app'].client_queries.wallets(cid)==before
    assert 'Клиент &lt;Тест&gt;' in ''.join(await finance['app'].client_directory.build_clients_chunks(group='Партнер'))
    report=''.join(await finance['app'].client_balances.build_report(code_filter='THB_BANK',sign_filter='−'))
    assert '-100.00 безнал' in report
    with pytest.raises(InvalidPosting,match='нулевыми|остатк'):
        await finance['app'].clients.close(key='close',actor_id=finance['actor'],client_id=cid,reason='Test')
    await groups.assign_accounting(text='/группа Баланс',key='balance-group',actor_id=finance['actor'],chat_id=-123)
    assert (await finance['app'].loans.balances())['items']==[]


async def test_wallet_crm_statement_and_zero_share_application_services(finance):
    cid=await client(finance)
    api=create_thai_finance_api(container=finance['app'],jwt_secret='x'*32)
    token=create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as http:
        assert (await http.get(f'/api/v1/clients/{cid}/statement')).status_code==401
        http.headers['Authorization']='Bearer '+token
        posted=await http.post(f'/api/v1/clients/{cid}/wallets/USDT/adjustments',json={
            'idempotency_key':'api-adjust','effective_at':INSTANT.isoformat(),'quantity':'1.123456','quoted_rate':'1'})
        assert posted.status_code==200,posted.text
        rows=(await http.get(f'/api/v1/clients/{cid}/statement')).json()
        assert Decimal(rows[0]['amount'])==Decimal('1.123456')
        preview=await http.post(f'/api/v1/clients/{cid}/zero-preview',json={'idempotency_key':'api-zero'})
        assert preview.status_code==200,preview.text
        result=await http.post(f'/api/v1/clients/{cid}/zero',json={'idempotency_key':'api-zero','effective_at':INSTANT.isoformat()})
        assert result.status_code==200,result.text
        assert (await balance(finance,cid))['balance']==0


async def test_zero_empty_decline_and_closed_month(finance):
    cid=await client(finance)
    await finance['app'].wallet_interactions.prepare_zero(key='decline',actor_id=finance['actor'],client_id=cid)
    assert (await finance['app'].wallet_interactions.resolve_zero(**common(finance,'decline'),confirmed=False))['cancelled']
    assert await finance['pool'].fetchval('SELECT is_active FROM clients WHERE id=$1',cid)
    await adjust(finance,cid,'seed')
    await finance['app'].wallet_interactions.prepare_zero(key='closed',actor_id=finance['actor'],client_id=cid)
    await finance['pool'].execute("INSERT INTO period_locks(context,period) VALUES ('ledger','2026-10-01')")
    with pytest.raises(PeriodClosed):
        await finance['app'].wallet_interactions.resolve_zero(**common(finance,'closed'),confirmed=True)
    assert (await balance(finance,cid))['balance']==10
    # Zero-valued clients close through the same audited command without a fake financial operation.
    empty=(await finance['app'].clients.create_client(key='empty-client',actor_id=finance['actor'],name='Empty'))['client_id']
    await finance['app'].wallet_interactions.prepare_zero(key='empty',actor_id=finance['actor'],client_id=empty)
    result=await finance['app'].wallet_interactions.resolve_zero(**common(finance,'empty'),confirmed=True)
    assert result['operation_id'] is None and not result['cancelled']
    assert not await finance['pool'].fetchval('SELECT is_active FROM clients WHERE id=$1',empty)


async def test_wallet_router_buttons_export_undo_and_zero_use_existing_handlers(finance,monkeypatch):
    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, User
    cid=await client(finance)
    commands=ThaiTelegramCommands(finance['app'],finance['config'])
    bot=Bot('123456:TEST')
    sent=telegram()
    answer=AsyncMock()
    document=AsyncMock()
    monkeypatch.setattr(bot,'send_message',sent.send_message)
    monkeypatch.setattr(bot,'edit_message_text',sent.edit_message_text)
    monkeypatch.setattr(Message,'answer',answer)
    monkeypatch.setattr(Message,'answer_document',document)
    monkeypatch.setattr(Message,'edit_text',AsyncMock())
    monkeypatch.setattr(CallbackQuery,'answer',AsyncMock())
    user=User(id=12345,is_bot=False,first_name='Manager')
    chat=Chat(id=-123,type='supergroup',title='Клиент')
    router=create_router(commands)
    async def send(text,mid):
        message=Message(message_id=mid,date=INSTANT,chat=chat,from_user=user,text=text).as_(bot)
        await router.message.handlers[0].callback(message)
    async def callback(name,data,mid=901):
        card=Message(message_id=mid,date=INSTANT,chat=chat).as_(bot)
        cq=CallbackQuery(id='test',from_user=user,message=card,chat_instance='test',data=data).as_(bot)
        handler=next(h.callback for h in router.callback_query.handlers if h.callback.__name__==name)
        await handler(cq)
    try:
        await send('/USDT -1.123456',100)
        assert 'Запомнил.' in sent.send_message.call_args.args[1]
        markup=sent.send_message.call_args.kwargs['reply_markup']
        assert markup.inline_keyboard[0][0].text=='Откатить изменение'
        await send('/кошелек',101)
        markup=answer.call_args.kwargs['reply_markup']
        assert [r[0].callback_data for r in markup.inline_keyboard]==['stmt:month','stmt:all']
        await send('/выписка USDT',102)
        assert 'остаток -1.123456' in answer.call_args.args[0]
        await callback('statement','stmt:all')
        assert document.await_count==1
        file=document.call_args.args[0]
        with ZipFile(BytesIO(file.data)) as archive:
            assert 'Asia/Bangkok' in archive.read('xl/sharedStrings.xml').decode()
        await callback('undo','undo:USDT:+:999999')
        assert (await balance(finance,cid))['balance']==0
        assert sent.edit_message_text.call_args.kwargs['reply_markup'] is None
        await send('/обнулить',103)
        assert answer.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data=='zero:yes:103'
        await callback('zero','zero:no:103')
        assert await finance['pool'].fetchval('SELECT is_active FROM clients WHERE id=$1',cid)
        await send('/группа -123 Партнер',104)
        assert await finance['pool'].fetchval('SELECT group_code FROM clients WHERE id=$1',cid)=='partner'
        await send('/клиенты Партнер',105)
        assert 'Клиент' in answer.call_args.args[0]
    finally:
        await bot.session.close()
