import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import asyncpg
import pytest
from aiogram.types import Chat, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.setup_handlers import create_router
from app.thai_russian_commands import HELP, WELCOME
from app.thai_telegram import ThaiTelegramCommands
from db_asyncpg.thai_clients.repository import ClientsRepository
from domain.finance.models import IdempotencyConflict, InvalidPosting
from tests.thai.test_posting import INSTANT


def customer(finance):
    commands = ThaiTelegramCommands(finance['app'], finance['config'])

    async def send(text='/start', *, message_id=1, chat_id=-999, user_id=999,
                   chat_name='Клиент Иван'):
        return await commands.execute(
            telegram_user_id=user_id, chat_id=chat_id, message_id=message_id,
            text=text, chat_name=chat_name, effective_at=INSTANT,
        )

    return commands, send


@pytest.mark.parametrize('chat_id', [-999, 999])
async def test_start_creates_customer_and_eight_wallets_without_staff_access(finance, chat_id):
    _, send = customer(finance)
    db, app = finance['pool'], finance['app']
    users_before = await db.fetchval('SELECT count(*) FROM users')
    assert await send(chat_id=chat_id) == WELCOME
    client_id = await app.trading_queries.client_by_chat(chat_id)
    assert client_id is not None
    assert await db.fetchval('SELECT group_code FROM clients WHERE id=$1', client_id) == 'balance'
    wallets = await app.client_queries.wallets(client_id)
    assert {row['instrument_code'] for row in wallets} == {
        'THB_CASH', 'THB_BANK', 'USDT', 'USD_WH', 'USD_BL', 'EUR', 'EUR500', 'RUB',
    }
    assert all(row['balance'] == 0 for row in wallets)
    assert await db.fetchval('SELECT count(*) FROM users') == users_before
    assert await app.queries.actor_by_telegram_id(999) is None
    assert await db.fetchval('SELECT count(*) FROM financial_operations') == 0
    assert await db.fetchval('SELECT count(*) FROM telegram_client_bootstrap_events') == 1
    assert await send('/дай', message_id=2, chat_id=chat_id) == 'Все счета нулевые. Посмотреть всё: /кошелек'
    assert '0.00 безнал' in await send('/дай безнал', message_id=3, chat_id=chat_id)
    for command in ('/пбат 3500 от 100', '/USDT 100', '/кошелек'):
        with pytest.raises(InvalidPosting, match='Нет доступа'):
            await send(command, message_id=4, chat_id=chat_id)


async def test_concurrent_and_repeated_start_do_not_duplicate_customer(finance):
    _, send = customer(finance)
    await asyncio.gather(*(send(message_id=n, user_id=999+n) for n in range(1, 9)))
    await send(message_id=1, user_id=1000)
    db = finance['pool']
    assert await db.fetchval('SELECT count(*) FROM clients') == 1
    assert await db.fetchval('SELECT count(*) FROM client_chat_links') == 1
    assert await db.fetchval('SELECT count(*) FROM client_wallet_accounts') == 8
    assert await db.fetchval('SELECT count(*) FROM telegram_client_bootstrap_events') == 8
    with pytest.raises(IdempotencyConflict):
        await send(message_id=1, user_id=1000, chat_name='Другие данные')


async def test_start_preserves_registered_customer_group_and_negative_wallet(finance):
    _, send = customer(finance)
    app, db = finance['app'], finance['pool']
    registered = await app.clients.create_client(
        key='registered', actor_id=finance['actor'], name='Старое имя', chat_id=-999,
    )
    client_id = registered['client_id']
    await app.clients.assign_group(key='partner', actor_id=finance['actor'], client_id=client_id,
                                   group_code='partner', reason='Партнер')
    commands = ThaiTelegramCommands(app, finance['config'])
    await commands.execute(telegram_user_id=12345, chat_id=-999, message_id=10,
                           text='/USDT -10', effective_at=INSTANT)
    await send(chat_name='Новое имя')
    assert await app.trading_queries.client_by_chat(-999) == client_id
    assert await db.fetchval('SELECT group_code FROM clients WHERE id=$1', client_id) == 'partner'
    assert await app.client_queries.chat_name(-999) == 'Новое имя'
    assert '-10.000000 usdt' in await send('/дай', message_id=2, chat_name=None)
    assert await app.client_queries.chat_name(-999) == 'Новое имя'
    assert await db.fetchval('SELECT count(*) FROM client_wallet_accounts') == 8
    assert await db.fetchval('SELECT count(*) FROM financial_operations') == 1


async def test_closed_chat_cannot_reopen_through_start_or_balance(finance):
    _, send = customer(finance)
    await send()
    app = finance['app']
    client_id = await app.trading_queries.client_by_chat(-999)
    await app.clients.close(key='close', actor_id=finance['actor'], client_id=client_id,
                            reason='Закрыт клиентом')
    for command in ('/start', '/дай'):
        with pytest.raises(InvalidPosting, match='закрыт'):
            await send(command, message_id=2)
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients') == 1
    assert not await finance['pool'].fetchval('SELECT is_active FROM clients WHERE id=$1', client_id)


async def test_bootstrap_rolls_back_customer_and_wallets_when_audit_fails(finance, monkeypatch):
    _, send = customer(finance)
    monkeypatch.setattr(ClientsRepository, 'save_bootstrap', AsyncMock(side_effect=RuntimeError('audit unavailable')))
    with pytest.raises(RuntimeError, match='audit unavailable'):
        await send()
    for table in ('clients', 'client_chat_links', 'client_wallet_accounts', 'telegram_client_bootstrap_events'):
        assert await finance['pool'].fetchval(f'SELECT count(*) FROM {table}') == 0


async def test_registration_audit_is_immutable(finance):
    _, send = customer(finance)
    await send()
    for sql in ('DELETE FROM telegram_client_bootstrap_events',
                "UPDATE telegram_client_bootstrap_events SET name_snapshot='Changed'"):
        with pytest.raises(asyncpg.PostgresError):
            await finance['pool'].execute(sql)
    assert await finance['pool'].fetchval('SELECT count(*) FROM telegram_client_bootstrap_events') == 1


async def test_public_help_wallet_and_keyboard_do_not_create_customer(finance):
    _, send = customer(finance)
    assert await send('/помоги') == WELCOME
    assert await send('/help') == HELP
    assert 'SKYEX' in await send('/info')
    assert 'менеджера' in await send('/кош')
    assert 'включена' in await send('/кнопки')
    assert 'скрыта' in await send('/скрыть')
    config = replace(finance['config'], usdt_wallet='TTestConfiguredWallet')
    commands = ThaiTelegramCommands(finance['app'], config)
    assert await commands.execute(telegram_user_id=999, chat_id=-999, message_id=1,
                                  text='/кош', effective_at=INSTANT) == 'TTestConfiguredWallet'
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients') == 0


async def test_router_uses_chat_title_and_restores_original_keyboard_behavior(finance, monkeypatch):
    commands, _ = customer(finance)
    handler = create_router(commands).message.handlers[0].callback
    answer = AsyncMock()
    monkeypatch.setattr(Message, 'answer', answer)
    from aiogram.types import User
    user = User(id=999, is_bot=False, first_name='Иван')
    for text, markup in (('/start', ReplyKeyboardRemove), ('/кнопки', ReplyKeyboardMarkup),
                         ('/скрыть', ReplyKeyboardRemove)):
        message = Message(message_id=1, date=INSTANT, text=text, from_user=user,
                          chat=Chat(id=-999, type='supergroup', title='Клиентский чат'))
        await handler(message)
        assert isinstance(answer.call_args.kwargs['reply_markup'], markup)
    assert await finance['app'].client_queries.chat_name(-999) == 'Клиентский чат'
