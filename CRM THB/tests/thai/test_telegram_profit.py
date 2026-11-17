from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.setup_handlers import create_router
from app.thai_telegram import ThaiTelegramCommands
from domain.finance.models import IdempotencyConflict, InvalidPosting, PermissionDenied
from tests.thai.test_posting import INSTANT, balance, fund


async def client(finance, name, key):
    return (await finance['app'].clients.create_client(
        key=key, actor_id=finance['actor'], name=name))['client_id']


async def send(finance, text, mid=1, **options):
    return await ThaiTelegramCommands(finance['app'],finance['config']).execute(
        telegram_user_id=12345, chat_id=-999, message_id=mid, text=text,
        effective_at=INSTANT, **options)


async def test_command_creates_same_profit_deal_without_cash_or_wallet_changes(finance):
    cid = await client(finance, 'Иван Петров', 'client')
    text='/прибыль 125,12345678 "Иван Петров" 100000,25 ! Историческая сделка'
    response=await send(finance,text)
    assert 'Запись прибыли #' in response and '125.12345678 USDT' in response
    assert await send(finance,text)==response
    with pytest.raises(IdempotencyConflict):
        await send(finance,text.replace('125,12345678','126'))
    row=await finance['pool'].fetchrow("SELECT * FROM effective_deals WHERE kind='manual_profit'")
    detail=await finance['app'].trading_queries.detail(row['id'])
    assert detail['client_id']==cid
    assert detail['attributes']['amount_usdt']=='125.12345678'
    assert detail['attributes']['thb_amount']=='100000.25'
    assert detail['attributes']['comment']=='Историческая сделка'
    assert detail['attributes']['business_date']==INSTANT.date().isoformat()
    assert await finance['pool'].fetchval('SELECT count(*) FROM deals')==1
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==1
    assert await finance['pool'].fetchval('SELECT count(*) FROM inventory_movements')==0
    assert all(row['balance']==0 for row in await finance['app'].client_queries.wallets(cid))
    report=await finance['app'].profit_queries.report(INSTANT.date().replace(day=1))
    assert report['net_profit_usdt']==Decimal('125.12345678')


async def test_optional_fields_loss_and_old_month_syntax_is_now_an_amount(finance):
    await send(finance,'/прибыль 10.2026')
    await send(finance,'/прибыль -2 ! Убыток',mid=2)
    await send(finance,'/прибыль 5 100000 ! Без клиента',mid=3)
    report=await finance['app'].profit_queries.report(INSTANT.date().replace(day=1))
    assert report['net_profit_usdt']==Decimal('13.2026')
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients')==0
    rows=await finance['pool'].fetch("SELECT client_id FROM deals WHERE kind='manual_profit'")
    assert all(row['client_id'] is None for row in rows)
    with pytest.raises(InvalidPosting,match='/прибыль'):
        await send(finance,'/прибыль',mid=4)
    with pytest.raises(InvalidPosting):
        await send(finance,'/прибыль отчет 10.2026',mid=5)
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==3


async def test_exact_clients_duplicate_names_and_id_resolution(finance):
    first=await client(finance,'Роджер','first')
    await client(finance,'Роджер','second')
    for text in ('/прибыль 10 Роджер','/прибыль 10 Неизвестный','/прибыль 10 #999999',
                 '/прибыль 10 #0','/прибыль 10 #9223372036854775808'):
        with pytest.raises(InvalidPosting):
            await send(finance,text)
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==0
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients')==2
    await send(finance,f'/прибыль 10 #{first}')
    assert await finance['pool'].fetchval("SELECT client_id FROM deals WHERE kind='manual_profit'")==first


async def test_exact_cyrillic_match_without_telegram_chat_and_retry_after_rename(finance):
    cid=await client(finance,'Роджер','client')
    response=await send(finance,'/прибыль 10 рОДЖЕР')
    await finance['pool'].execute("UPDATE parties SET name='Новое имя' WHERE id=(SELECT party_id FROM clients WHERE id=$1)",cid)
    assert await send(finance,'/прибыль 10 рОДЖЕР')==response
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==1
    assert await finance['pool'].fetchval("SELECT client_id FROM deals WHERE kind='manual_profit'")==cid


async def test_authorization_and_bangkok_date(finance):
    commands=ThaiTelegramCommands(finance['app'],finance['config'])
    with pytest.raises(InvalidPosting,match='Нет доступа'):
        await commands.execute(telegram_user_id=999,chat_id=-999,message_id=1,
            text='/прибыль 10',effective_at=INSTANT)
    cashier=await finance['pool'].fetchval("INSERT INTO users(telegram_user_id,display_name) VALUES (777,'Cashier') RETURNING id")
    await finance['pool'].execute("INSERT INTO user_roles VALUES ($1,'cashier')",cashier)
    with pytest.raises(PermissionDenied):
        await commands.execute(telegram_user_id=777,chat_id=-999,message_id=1,
            text='/прибыль 10',effective_at=INSTANT)
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==0
    await commands.execute(telegram_user_id=12345,chat_id=-999,message_id=1,
        text='/прибыль 10',effective_at=datetime(2026,10,8,20,tzinfo=UTC))
    assert str(await finance['pool'].fetchval('SELECT business_date FROM financial_operations'))=='2026-10-09'


async def test_handler_keeps_free_comment_literal_and_never_sends_html(finance,monkeypatch):
    from aiogram import Bot
    from aiogram.types import Chat, Message, User

    answer=AsyncMock()
    monkeypatch.setattr(Message,'answer',answer)
    bot=Bot('123456:TEST')
    commands=ThaiTelegramCommands(finance['app'],replace(finance['config'],operations_chat_id=-999))
    router=create_router(commands)
    message=Message(message_id=7,date=INSTANT,chat=Chat(id=-999,type='supergroup',title='Админы'),
        from_user=User(id=12345,is_bot=False,first_name='Manager'),
        text='/прибыль@thai_bot -20 ! Клиент сказал "верну <b>завтра</b>').as_(bot)
    try:
        await router.message.handlers[0].callback(message)
        assert answer.await_count==1
        assert answer.call_args.kwargs['parse_mode'] is None
        row=await finance['pool'].fetchval("SELECT attributes->>'comment' FROM deals WHERE kind='manual_profit'")
        assert row=='Клиент сказал "верну <b>завтра</b>'
    finally:
        await bot.session.close()


async def test_named_closure_and_payment_subcommands_are_preserved(finance):
    await send(finance,'/прибыль 10')
    await fund(finance,instrument='USDT',quantity=Decimal('100'))
    commands=ThaiTelegramCommands(finance['app'],finance['config'])
    closed=await commands.execute(telegram_user_id=12345,chat_id=-999,message_id=2,
        text='/прибыль закрыть 10.2026',effective_at=datetime(2026,11,2,tzinfo=UTC))
    assert 'Период закрыт' in closed
    paid=await commands.execute(telegram_user_id=12345,chat_id=-999,message_id=3,
        text='/прибыль выплата скайекс USDT 5',effective_at=datetime(2026,11,2,tzinfo=UTC))
    assert 'Выплата проведена' in paid
    assert await balance(finance,'USDT') == (95,95)
