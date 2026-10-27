from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.setup_handlers import create_router
from app.thai_telegram import ThaiTelegramCommands
from db_asyncpg.thai_reports import ReportQueries
from domain.finance.models import InsufficientCash, InvalidPosting, PeriodClosed, PermissionDenied
from tests.thai.test_expenses import common
from tests.thai.test_internal_exchange_rates import customer, exchange
from tests.thai.test_posting import INSTANT, balance
from tests.thai.test_wallet_workflows import telegram

CASH_CHAT=-5009229749
OTHER_CASH=-5009229750
OPERATIONS=-900


def commands(finance):
    config=replace(finance['config'],city_cash_chat_map={'тай':CASH_CHAT,'тай-дубль':OTHER_CASH},operations_chat_id=OPERATIONS)
    return ThaiTelegramCommands(finance['app'],config)


async def send(bot,text,mid=1,*,chat=CASH_CHAT,user=12345):
    return await bot.execute(telegram_user_id=user,chat_id=chat,message_id=mid,text=text,
        effective_at=INSTANT,chat_name='Касса Тай',actor_name='Manager')


async def test_cash_views_start_and_customer_commands_never_create_a_client(finance):
    bot=commands(finance)
    for text in ('/start','/кошелек','/кошелёк','/дай','/дай бат'):
        result=await send(bot,text)
        assert 'касс' in result.lower()
    for text in ('/регистрация Касса','/привязать 1','/обнулить','/закрыть x','/перевод 100 Client','/депбат 100','/пбат 3500 от 100','/бат'):
        with pytest.raises(InvalidPosting):
            await send(bot,text)
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients')==0
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==0
    with pytest.raises(InvalidPosting,match='Нет доступа'):
        await send(bot,'/start',user=999)


@pytest.mark.parametrize('code',['THB_CASH','THB_BANK','USDT','USD_WH','USD_BL','EUR','EUR500','RUB'])
async def test_cash_adjustment_all_instruments_no_client_capital_or_profit(finance,code):
    bot=commands(finance)
    text=f'/{code} 100 ! Остаток кассы'
    await send(bot,text,100)
    await send(bot,text,100)
    assert (await balance(finance,code))[0]==100
    await send(bot,f'/{code} -40',101)
    assert (await balance(finance,code))[0]==60
    assert '60' in await send(bot,'/дай '+code,chat=OTHER_CASH)
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients')==0
    assert await finance['pool'].fetchval('SELECT count(*) FROM capital_contributions')==0
    assert await finance['pool'].fetchval("SELECT coalesce(sum(l.amount_usdt),0) FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id WHERE a.account_class IN ('income','expense')")==0
    assert (await ReportQueries(finance['pool']).dashboard(INSTANT.date().replace(day=1)))['capital_usdt']==0


async def test_cash_rejects_overdraft_invalid_client_tail_and_closed_period(finance):
    bot=commands(finance)
    await send(bot,'/бат 100',1)
    with pytest.raises(InsufficientCash):
        await send(bot,'/бат -101',2)
    with pytest.raises(InvalidPosting,match='номер заявки'):
        await send(bot,'/бат 100 Клиент',3)
    await finance['pool'].execute("INSERT INTO period_locks(context,period) VALUES ('ledger','2026-10-01')")
    with pytest.raises(PeriodClosed):
        await send(bot,'/бат 1',4)
    assert (await balance(finance,'THB_CASH'))[0]==100


async def test_cash_correction_uses_internal_quote_and_is_retry_safe(finance):
    bot=commands(finance)
    cid=await customer(finance)
    await exchange(finance,cid,'first','THB_CASH','3500','USDT','100')
    await send(bot,'/бат 350',1)
    assert await balance(finance,'THB_CASH')==(350,10)
    await exchange(finance,cid,'second','USDT','100','THB_CASH','3700')
    await send(bot,'/бат 350',1)
    assert await balance(finance,'THB_CASH')==(350,10)


async def test_cash_adjustment_card_undo_is_idempotent_and_checks_message_owner(finance):
    bot=commands(finance)
    await send(bot,'/бат 100',1)
    operation=await finance['pool'].fetchval("SELECT id FROM financial_operations WHERE command_details->>'purpose'='cash_adjustment'")
    telegram_bot=telegram()
    delivery=bot._delivery(telegram_bot)
    await delivery.deliver_wallet_adjustment(operation)
    await delivery.deliver_wallet_adjustment(operation)
    assert telegram_bot.send_message.await_count==1
    link=await finance['pool'].fetchrow("SELECT * FROM telegram_message_links WHERE purpose='cash_adjustment'")
    with pytest.raises(InvalidPosting,match='не найдена'):
        await finance['app'].manual_cash.undo_by_message(**{k:v for k,v in common(finance,'ignored').items() if k!='key'},chat_id=OTHER_CASH,message_id=link['message_id'])
    args={**common(finance,'ignored'),'chat_id':CASH_CHAT,'message_id':link['message_id']}
    args.pop('key')
    first=await finance['app'].manual_cash.undo_by_message(**args)
    assert await finance['app'].manual_cash.undo_by_message(**args)==first
    await delivery.deliver_wallet_adjustment(operation)
    assert 'Отменено' in telegram_bot.edit_message_text.call_args.args[0]
    assert await balance(finance,'THB_CASH')==(0,0)


async def test_cash_permissions_and_statement_include_real_cash_movements_only(finance):
    bot=commands(finance)
    actor=await finance['pool'].fetchval("INSERT INTO users(display_name,telegram_user_id) VALUES ('Reader',999) RETURNING id")
    await finance['pool'].execute("INSERT INTO user_roles VALUES ($1,'accountant')",actor)
    # An accountant can read and post ordinary finance, but lacks cash.execute.
    await send(bot,'/кошелек',user=999)
    with pytest.raises(PermissionDenied):
        await send(bot,'/USDT 100',user=999)
    await send(bot,'/USDT 100',1)
    await send(bot,'/USDT -25',2)
    cid=await customer(finance)
    await finance['app'].wallet_interactions.accounting_adjust(**common(finance,'client-only'),client_id=cid,instrument='USDT',quantity='500')
    rows=await finance['app'].queries.cash_history('USDT',limit=1)
    assert len(rows)==1 and rows[0]['balance_after']==75 and rows[0]['quantity_delta']==-25
    older=await finance['app'].queries.cash_history('USDT',before_id=rows[0]['id'])
    assert older[0]['balance_after']==100
    exported=await finance['app'].queries.export_cash_transactions()
    assert [row['amount'] for row in exported]==[100,-25]
    assert 'остаток 75' in await send(bot,'/выписка USDT')


async def test_cash_chat_can_settle_client_request_separately_from_request_chat(finance):
    bot=commands(finance)
    await send(bot,'/депбат 3500',1,chat=-123)
    deal=await bot.cash_request_by_command(-123,1)
    telegram_bot=telegram()
    await bot.deliver_cash_request(telegram_bot,deal)
    chats={call.args[0] for call in telegram_bot.send_message.call_args_list}
    assert {CASH_CHAT,OTHER_CASH,OPERATIONS,-123}<=chats
    call=next(call for call in telegram_bot.send_message.call_args_list if call.args[0]==CASH_CHAT)
    button=call.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
    link=await finance['pool'].fetchrow("SELECT * FROM telegram_message_links WHERE deal_id=$1 AND chat_id=$2",deal,CASH_CHAT)
    await bot.cash_status(telegram_user_id=12345,chat_id=CASH_CHAT,message_id=link['message_id'],data=button)
    await send(bot,f'/бат 3500 Б-{deal:06d}',3)
    await send(bot,f'/бат 3500 Б-{deal:06d}',4)
    assert (await balance(finance,'THB_CASH'))[0]==3500
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients')==1
    # A client chat cannot masquerade as the configured cashier.
    with pytest.raises(InvalidPosting,match='рабочем чате'):
        await send(bot,f'/бат 3500 Б-{deal:06d}',5,chat=-123)
    rows=await finance['app'].queries.cash_history('THB_CASH')
    assert len(rows)==1


async def test_cash_chat_real_handler_and_statement_callback_are_staff_only(finance):
    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, User

    from handlers.wallets import WalletsHandler
    bot=commands(finance)
    create_router(bot)
    transport=Bot('12345:test')
    manager=User(id=12345,is_bot=False,first_name='Manager')
    message=Message(message_id=1,date=INSTANT,chat=Chat(id=CASH_CHAT,type='group',title='Касса'),from_user=manager,text='/USDT 100').as_(transport)
    # Patch only Telegram delivery; the handler and financial services stay real.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Bot,'send_message',AsyncMock(return_value=SimpleNamespace(message_id=900)))
        assert await WalletsHandler.accounting_cash_chat_command(bot,message,'/usdt')
        assert (await balance(finance,'USDT'))[0]==100
        assert await finance['pool'].fetchval('SELECT count(*) FROM clients')==0
    router=create_router(bot)
    statement=next(h.callback for h in router.callback_query.handlers if h.callback.__name__=='statement')
    unknown=User(id=888,is_bot=False,first_name='Unknown')
    callback=CallbackQuery(id='x',from_user=unknown,chat_instance='x',message=message,data='stmt:all').as_(transport)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Bot,'__call__',AsyncMock(return_value=True))
        patch.setattr(Bot,'send_document',AsyncMock())
        await statement(callback)
        assert not Bot.send_document.called
        assert Bot.__call__.call_args.args[0].show_alert
    await transport.session.close()


async def test_reserved_cash_chat_cannot_be_bound_through_crm_or_bootstrap(finance):
    from app.container import ApplicationContainer
    config=commands(finance)._config
    app=ApplicationContainer.build(finance['pool'],config=config)
    with pytest.raises(InvalidPosting,match='общей кассы'):
        await app.clients.create_client(key='forbidden',actor_id=finance['actor'],name='Cash',chat_id=CASH_CHAT)
    cid=(await app.clients.create_client(key='normal',actor_id=finance['actor'],name='Client'))['client_id']
    with pytest.raises(InvalidPosting,match='общей кассы'):
        await app.clients.bind_chat(key='bind',actor_id=finance['actor'],client_id=cid,chat_id=CASH_CHAT)
    with pytest.raises(InvalidPosting,match='общей кассы'):
        await app.client_bootstrap.ensure_telegram_client_wallet(key='start',telegram_user_id=12345,chat_id=CASH_CHAT,chat_name='Cash')
    assert await finance['pool'].fetchval('SELECT count(*) FROM client_chat_links')==0


async def test_cash_chat_balance_and_crm_expense_share_one_cash_without_capital(finance):
    bot=commands(finance)
    await send(bot,'/бат 3500',1)
    category=await finance['pool'].fetchval('SELECT id FROM expense_categories ORDER BY id LIMIT 1')
    await finance['app'].expenses.record(**common(finance,'expense'),category_id=category,instrument='THB_CASH',
        amount='350',quoted_rate='35',payer='business',shares={'common':'100'})
    assert await balance(finance,'THB_CASH')==(3150,90)
    assert '3’150' in await send(bot,'/дай бат')
    assert await finance['pool'].fetchval('SELECT count(*) FROM capital_contributions')==0
    assert (await finance['app'].profit_queries.report(INSTANT.date().replace(day=1)))['net_profit_usdt']==-10
    await finance['app'].projections.rebuild(finance['actor'])
    assert await balance(finance,'THB_CASH')==(3150,90)


async def test_two_sided_request_from_cash_chat_posts_only_after_both_sides(finance):
    bot=commands(finance)
    await send(bot,'/USDT 100',1)
    await send(bot,'/пбатвт 3500 100',2,chat=-123)
    deal=await bot.cash_request_by_command(-123,2)
    await send(bot,f'/готово Б-{deal:06d}',3,chat=OPERATIONS)
    await send(bot,f'/бат 3500 Б-{deal:06d}',4)
    assert await balance(finance,'THB_CASH')==(0,0)
    assert await balance(finance,'USDT')==(100,100)
    await send(bot,f'/USDT -100 Б-{deal:06d}',5)
    assert (await balance(finance,'THB_CASH'))[0]==3500
    assert await balance(finance,'USDT')==(0,0)
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind='fx_trade'")==1


@pytest.mark.parametrize('role',['manager','owner','admin'])
async def test_each_editing_role_can_operate_common_cash(finance,role):
    await finance['pool'].execute('DELETE FROM user_roles WHERE user_id=$1',finance['actor'])
    await finance['pool'].execute('INSERT INTO user_roles(user_id,role) VALUES ($1,$2)',finance['actor'],role)
    await send(commands(finance),'/USDT 100')
    assert await balance(finance,'USDT')==(100,100)
