from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.setup_handlers import create_router
from app.thai_russian_commands import HELP, INSTRUMENTS, amount, instrument, month
from app.thai_telegram import ThaiTelegramCommands
from domain.finance.models import IdempotencyConflict, InvalidPosting
from tests.thai.test_posting import INSTANT, balance, fund
from tests.thai.test_profit import configure


def bot(finance):
    commands = ThaiTelegramCommands(finance["app"], finance["config"])
    counter = 0

    async def send(text, *, message_id=None, chat_id=-123, **kwargs):
        nonlocal counter
        counter += 1
        return await commands.execute(
            telegram_user_id=12345, chat_id=chat_id,
            message_id=message_id if message_id is not None else counter,
            text=text, effective_at=INSTANT, **kwargs,
        )

    return commands, send


async def post_cash(send, finance, text, *, message_id):
    """Exercise the complete request/ready/actual cash cycle for financial regressions."""
    await send(text, message_id=message_id)
    deal_id = await finance['pool'].fetchval('SELECT id FROM deals WHERE external_reference=$1', f'tg:-123:{message_id}:cash-request')
    await finance['app'].cash_requests.change_request_status(key=f'test-ready:{deal_id}',
        actor_id=finance['actor'],request_id=f'Б-{deal_id:06d}',action='ready')
    deal = await finance['app'].trading_queries.detail(deal_id)
    attrs = deal['attributes']
    signed = ('-' if deal['kind']=='withdrawal' else '') + attrs['quantity']
    return await send(f"/{attrs['instrument']} {signed} Б-{deal_id:06d}",message_id=100000+deal_id)


@pytest.mark.parametrize("alias,expected", [("USD", "USD_BL"), ("USDW", "USD_WH"),
    ("евро500", "EUR500"), ("бат", "THB_CASH"), ("безнал", "THB_BANK")])
def test_old_instrument_names(alias, expected):
    assert instrument(alias) == expected


def test_help_and_expression_contract():
    for command in ("/кошелек", "/дай", "/пбат", "/депбат", "/выдбат", "/перевод", "/бест", "/отпр", "/setwallet"):
        assert command in HELP
    for command in ("/bc", "/token", "/deposit", "/request", "/register", "/ордер"):
        assert command not in HELP
    assert amount("40,5к/35", "USDT") == Decimal("1157.142857")
    assert amount("(2+3*4)", "USDT") == 14
    assert amount("-1,005", "RUB", signed=True) == Decimal("-1.01")
    assert month("10.2026").isoformat() == "2026-10-01"
    for expression in ("1/0", "abc", "0", "-1", "1" * 201, "1**2"):
        with pytest.raises(InvalidPosting):
            amount(expression, "USDT")
    assert len(HELP) < 4096


async def test_russian_wallet_exchange_cash_and_retry(finance):
    _, send = bot(finance)
    await send("/регистрация Тестовый клиент")
    await send("/курс бат 35")
    assert "0.00 бат" in await send("/кошелек")
    assert await send("/дай") == "Все счета нулевые. Посмотреть всё: /кошелек"
    first = await send("/пбат 3600 от 100 срочно", message_id=100)
    assert await send("/пбат 3600 от 100 срочно", message_id=100) == first
    assert (await finance["app"].trading_queries.detail(1))["attributes"]["comment"] == "срочно"
    assert "-3’600.00 бат" in await send("/дай")
    assert "100.000000 usdt" in await send("/дай")
    assert await balance(finance, "THB_CASH") == (0, 0)
    assert await balance(finance, "USDT") == (0, 0)
    await post_cash(send, finance, "/депбат 3,6к ! касса", message_id=101)
    assert await balance(finance, "THB_CASH") == (3600, Decimal("102.85714286"))
    await post_cash(send, finance, "/депбат 3,6к ! касса", message_id=101)
    assert (await balance(finance, "THB_CASH"))[0] == 3600
    assert "остаток 0" in await send("/выписка бат")
    await post_cash(send, finance, "/выдбат 100", message_id=110)
    assert (await balance(finance, "THB_CASH"))[0] == 3500
    assert "-100.00 бат" in await send("/дай")
    with pytest.raises(IdempotencyConflict):
        await post_cash(send, finance, "/депбат 3700", message_id=101)


async def test_old_dollars_and_corrections_do_not_change_cash_or_profit(finance):
    _, send = bot(finance)
    await send("/регистрация Доллары")
    await send("/курс USD 1")
    await send("/курс USDW 1,01")
    await send("/пд 100 от 100")
    await send("/пб 100 от 101")
    wallet = await send("/дай")
    assert "-100.00 usd" in wallet and "-100.00 usdw" in wallet
    await send("/USD 100 возврат долга", message_id=200)
    await send("/USD 100 возврат долга", message_id=200)
    await send("/USDT (2+3*4) корректировка")
    await send("/курс руб 0,01")
    await send("/RUB -100")
    assert "0.00 usd" in await send("/кошелек")
    assert "215.000000 usdt" in await send("/дай")
    assert all(row["balance"] == 0 for row in await finance["app"].queries.cash_balances())
    report = await finance["app"].profit_queries.report(month("10.2026"))
    assert report["income_usdt"] == 0
    assert report["net_profit_usdt"] == 0


async def test_client_transfer_defaults_to_baht_and_keeps_cash(finance):
    from handlers.wallets import WalletsHandler
    from tests.thai.test_expenses import common
    _,send=bot(finance)
    await send('/регистрация Отправитель')
    await send('/регистрация Петров Иван',chat_id=-124)
    await send('/курс бат 35')
    source=await finance['app'].trading_queries.client_by_chat(-123)
    target=await finance['app'].trading_queries.client_by_chat(-124)
    amount,code,name=WalletsHandler._parse_transfer('/перевод 1к Петров Иван',
        default_currency='THB_CASH',aliases=INSTRUMENTS,
        allow_expressions=True)
    assert code=='THB_CASH' and amount==1000 and name=='Петров Иван'
    await finance['app'].client_transfers.prepare_accounting(key='request',actor_id=finance['actor'],
        source_client_id=source,source_chat_id=-123,instrument=code,quantity=amount,
        recipient_name=name,effective_at=INSTANT)
    assert 'Все счета нулевые' in await send('/дай')
    posted=await finance['app'].client_transfers.resolve_accounting(**common(finance,'request'),recipient_id=target,confirmed=True)
    assert await finance['app'].client_transfers.resolve_accounting(**common(finance,'request'),recipient_id=target,confirmed=True)==posted
    assert '-1’000.00 бат' in await send('/дай')
    assert '1’000.00 бат' in await send('/дай',chat_id=-124)
    assert await balance(finance,'THB_CASH')==(0,0)
    assert 'Петров Иван' in await send('/бк бат +')


async def test_future_market_rate_is_not_needed_to_settle_exchange_debt(finance):
    _, send = bot(finance)
    await send("/регистрация Курсы")
    await send("/пбат 3500 от 100")
    assert await finance["pool"].fetchval("SELECT count(*) FROM deals") == 1
    await finance["app"].trading.set_rate(
        key="future", actor_id=finance["actor"], instrument="THB_CASH", quoted_rate="35",
        business_date=(INSTANT + timedelta(days=1)).date(), effective_at=INSTANT + timedelta(days=1),
    )
    await post_cash(send,finance,"/депбат 3500",message_id=100)
    assert await balance(finance, "THB_CASH") == (3500, 100)


async def test_russian_bestchange_and_report(finance):
    _, send = bot(finance)
    await configure(finance)
    await fund(finance, instrument="USDT", quantity=Decimal(100))
    first = await send("/бест продажа 100 35 36 бат", message_id=100)
    assert await send("/бест продажа 100 35 36 бат", message_id=100) == first
    assert "Доля Данияра: 1" in await send("/бест отчет 10.2026")
    assert "Чистая прибыль: 1" in await send("/прибыль 10.2026")


async def test_bot_authorization_auto_client_and_token_disabled(finance):
    commands, send = bot(finance)
    await send("/кошелек", chat_name="Новый чат")
    await send("/кошелек", chat_name="Новый чат")
    assert await finance["pool"].fetchval("SELECT count(*) FROM clients") == 1
    assert "Новый чат" in await send("/клиенты")
    with pytest.raises(InvalidPosting, match="Нет доступа"):
        await commands.execute(telegram_user_id=999, chat_id=-125, message_id=1,
                               text="/кошелек", effective_at=INSTANT, chat_name="Чужой")
    with pytest.raises(InvalidPosting, match="Вход в CRM через Telegram"):
        await send("/token", private=True)
    assert await finance["pool"].fetchval("SELECT count(*) FROM clients") == 1


async def test_cash_router_uses_existing_handler_and_authorized_status(finance, monkeypatch):
    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, User
    commands, _ = bot(finance)
    telegram = Bot('123456:TEST')
    sent = AsyncMock(return_value=SimpleNamespace(message_id=900))
    monkeypatch.setattr(telegram,'send_message',sent)
    monkeypatch.setattr(telegram,'edit_message_text',AsyncMock())
    answer = AsyncMock()
    monkeypatch.setattr(CallbackQuery,'answer',answer)
    chat = Chat(id=-123,type='supergroup',title='Клиент')
    user = User(id=12345,is_bot=False,first_name='Manager')
    source = Message(message_id=10,date=INSTANT,chat=chat,from_user=user,text='/депбат 100.25').as_(telegram)
    router = create_router(commands)
    await router.message.handlers[0].callback(source)
    assert sent.await_count==1
    assert 'Заявка на внесение' in sent.call_args.args[1]
    markup = sent.call_args.kwargs['reply_markup']
    assert markup.inline_keyboard[0][0].text=='Готово к расчету'
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')==0
    card = Message(message_id=900,date=INSTANT,chat=chat).as_(telegram)
    callback = CallbackQuery(id='test',from_user=User(id=999,is_bot=False,first_name='Other'),
        message=card,chat_instance='test',data=markup.inline_keyboard[0][0].callback_data).as_(telegram)
    await router.callback_query.handlers[0].callback(callback)
    assert answer.call_args.kwargs['show_alert']
    assert (await finance['app'].trading_queries.detail(1))['status']=='draft'
    callback = callback.model_copy(update={'from_user':user}).as_(telegram)
    await router.callback_query.handlers[0].callback(callback)
    assert (await finance['app'].trading_queries.detail(1))['status']=='ready'
    assert await balance(finance,'THB_CASH')==(0,0)
    await telegram.session.close()


async def set_all_rates(send):
    for alias, quote in (("бат", "35"), ("безнал", "35"), ("USD", "1"),
                         ("USDW", "1.01"), ("евро", "1.1"), ("евро500", "1.2"), ("руб", "0.01")):
        await send(f"/курс {alias} {quote}")


async def test_all_accept_short_pairs_classify_from_exchange_perspective(finance):
    from app.thai_russian_commands import PAY, RECEIVE
    from db_asyncpg.thai_trading.workspace import DealWorkspaceQueries

    _, send = bot(finance)
    await send("/регистрация Все направления")
    await set_all_rates(send)
    client_id = await finance["app"].trading_queries.client_by_chat(-123)
    from domain.deal_policy import exchange_type

    expected = {code: Decimal(0) for code in RECEIVE.values()}
    counts = {"sale": 0, "purchase": 0, "conversion": 0}
    posted = 0
    for command, source in RECEIVE.items():
        for marker, target in PAY.items():
            if source == target:
                continue
            result = await send(f"{command}@testexchange3bot 2*50 {marker} 10*5 тест {source} → {target}")
            kind = exchange_type(source, target)
            counts[kind] += 1
            assert result.startswith("<b>Заявка</b>: <code>")
            assert "<b>Получаем</b>" in result and "<b>Отдаём</b>" in result
            expected[source] -= 100
            expected[target] += 50
            actual = {row["instrument_code"]: row["balance"]
                      for row in await finance["app"].client_queries.wallets(client_id)}
            assert actual == expected
            posted += 1
    assert posted == 56
    assert all(row["balance"] == 0 for row in await finance["app"].queries.cash_balances())
    workspace = DealWorkspaceQueries(finance["pool"])
    assert counts == {"sale": 12, "purchase": 12, "conversion": 32}
    for kind, count in counts.items():
        result = await workspace.workspace(kind=kind, limit=100)
        assert len(result["items"]) == count
        assert result["summaries"] == [{"status": "posted", "count": count}]
        assert all(row["kind"] == kind and row["attributes"]["operation_type"] == kind
                   for row in result["items"])


async def test_all_deposit_withdraw_commands_confirm_and_appear_in_crm(finance):
    from app.thai_russian_commands import SUFFIXES
    from db_asyncpg.thai_trading.workspace import DealWorkspaceQueries
    _, send = bot(finance)
    await send("/регистрация Все валюты")
    await set_all_rates(send)
    sequence=100
    for suffix,code in SUFFIXES.items():
        for prefix,quantity,expected_balance in (("деп",200,200),("выд",50,150)):
            await post_cash(send,finance,f'/{prefix}{suffix} {quantity} ! {code}',message_id=sequence)
            assert (await balance(finance,code))[0]==expected_balance
            sequence+=1
    client_id=await finance['app'].trading_queries.client_by_chat(-123)
    assert all(row['balance']==150 for row in await finance['app'].client_queries.wallets(client_id))
    workspace=DealWorkspaceQueries(finance['pool'])
    for kind in ('deposit','withdrawal'):
        result=await workspace.workspace(kind=kind)
        assert len(result['items'])==8
        assert {row['attributes']['instrument'] for row in result['items']}==set(SUFFIXES.values())


async def test_corrected_trade_recalculates_type_from_replacement_pair(finance):
    from db_asyncpg.thai_trading.workspace import DealWorkspaceQueries

    _, send = bot(finance)
    await send("/регистрация Исправление")
    await send("/курс бат 35")
    await send("/пбат 3500 от 100")
    original = (await finance["app"].trading_queries.list())[0]
    await finance["app"].trading.reverse(
        key="correction", actor_id=finance["actor"], business_date=INSTANT.date(), effective_at=INSTANT,
        deal_id=original["id"], reason="Исправляем сумму",
        replacement={"mode": "wallet", "receive": {"instrument": "USDT", "quantity": "100", "quoted_rate": "1"},
                     "pay": {"instrument": "THB_BANK", "quantity": "3600", "quoted_rate": "35"}},
    )
    result = await DealWorkspaceQueries(finance["pool"]).workspace()
    assert {(row["status"], row["kind"]) for row in result["items"]} == {("cancelled", "sale"), ("draft", "purchase")}
    assert all(row["kind"] == row["attributes"]["operation_type"] for row in result["items"])


async def test_existing_bot_conversions_and_manual_trades_classify_correctly(finance):
    from db_asyncpg.thai_trading.workspace import DealWorkspaceQueries
    from tests.thai.test_trading import client, trade

    client_id = await client(finance)
    old = await finance["app"].trading.create(
        key="old-bot", actor_id=finance["actor"], client_id=client_id, mode="wallet",
        receive={"instrument": "THB_CASH", "quantity": "3500", "quoted_rate": "35"},
        pay={"instrument": "USDT", "quantity": "100", "quoted_rate": "1"}, external_reference="tg:-123:100",
    )
    manual = await trade(finance, mode="wallet", client_id=client_id, key="manual-sale")
    workspace = DealWorkspaceQueries(finance["pool"])
    # Previously stored incorrect labels must not override the actual pair.
    await finance["pool"].execute(
        "UPDATE deals SET attributes=jsonb_set(attributes,'{operation_type}','\"conversion\"') WHERE id=$1",
        old["deal_id"],
    )
    assert (await workspace.workspace(kind="conversion"))["items"] == []
    assert {row["deal_id"] for row in (await workspace.workspace(kind="sale"))["items"]} == {old["deal_id"], manual}


@pytest.mark.parametrize('text,code,quantity', [
    ('/пт 1000 об 1000*33', 'USD_WH', Decimal('33000')),
    ('/пт 1000 обат 1000*33', 'THB_CASH', Decimal('33000')),
    ('/пбат 1000*33 от 1000', 'THB_CASH', Decimal('-33000')),
])
async def test_short_exchange_derives_missing_valuation_from_usdt_leg(finance, text, code, quantity):
    _, send = bot(finance)
    await send('/start', chat_name='Клиент')
    first = await send(text, message_id=100)
    assert await send(text, message_id=100) == first
    client_id = await finance['app'].trading_queries.client_by_chat(-123)
    wallets = await finance['app'].client_queries.wallets(client_id)
    assert next(w['balance'] for w in wallets if w['instrument_code']==code) == quantity
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == 1
    assert all(row['balance']==0 for row in await finance['app'].queries.cash_balances())
    deal = await finance['app'].trading_queries.detail(1)
    assert Decimal(deal['attributes']['result_usdt']) == 0
    for side in ('receive','pay'):
        assert Decimal(deal['attributes'][side]['quoted_rate']) > 0


async def test_wallet_reuses_original_builder_and_router_html(finance, monkeypatch):
    from aiogram.types import Chat, Message, User

    from services.wallets.text_builder import WalletTextBuilder

    commands, send = bot(finance)
    await send('/start', chat_name='Иван <test>')
    await send('/пт 1000 обат 33000')
    client_id = await finance['app'].trading_queries.client_by_chat(-123)
    wallets = await finance['app'].client_queries.wallets(client_id)
    expected = WalletTextBuilder.wallet_text(chat_name='Иван <test>',
        rows=[{**row, 'currency_code':row['instrument_code']} for row in wallets])
    text = await send('/кошелек')
    assert text == expected
    assert 'Средств у Иван &lt;test&gt;:' in text
    assert '33’000.00 бат' in text and '-1’000.000000 usdt' in text
    assert text.startswith('<code>') and text.endswith('</code>')
    assert len(text.splitlines()) == 10
    answer = AsyncMock()
    monkeypatch.setattr(Message, 'answer', answer)
    message = Message(message_id=999, date=INSTANT, text='/кошелек',
        chat=Chat(id=-123, type='supergroup', title='Иван <test>'),
        from_user=User(id=12345, is_bot=False, first_name='Manager'))
    await create_router(commands).message.handlers[0].callback(message)
    assert answer.call_args.args[0] == expected
    assert answer.call_args.kwargs['parse_mode'] == 'HTML'


async def test_exchange_returns_original_client_card_and_html_balance(finance, monkeypatch):
    from aiogram import Bot
    from aiogram.types import Chat, Message, User

    commands, send = bot(finance)
    await send('/start', chat_name='Клиент <А>')
    card = await send('/пт 1000 обат 1000*33 <срочно>', message_id=100,
        chat_name='Клиент <А>', actor_name='Менеджер <М>')
    expected = (
        '<b>Заявка</b>: <code>1</code>\n-----\n'
        '<b>Получаем</b>: <code>1’000.000000 usdt</code>\n'
        '<b>Курс</b>: <code>33</code>\n'
        '<b>Отдаём</b>: <code>33’000.00 бат</code>\n'
        '----\n<b>Комментарий</b>: <code>&lt;срочно&gt;</code>❗️'
    )
    assert card == expected
    telegram = Bot('123456:TEST')
    sent = AsyncMock(return_value=SimpleNamespace(message_id=900))
    reply = AsyncMock()
    monkeypatch.setattr(telegram, 'send_message', sent)
    monkeypatch.setattr(Message, 'reply', reply)
    message = Message(message_id=100, date=INSTANT, text='/пт 1000 обат 1000*33 <срочно>',
        chat=Chat(id=-123, type='supergroup', title='Клиент <А>'),
        from_user=User(id=12345, is_bot=False, first_name='Менеджер <М>')).as_(telegram)
    await create_router(commands).message.handlers[0].callback(message)
    assert sent.await_count==1
    assert sent.call_args.args==(-123,expected)
    assert sent.call_args.kwargs['parse_mode']=='HTML'
    assert sent.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data=='req_cancel:1:1'
    summary = reply.call_args.args[0]
    assert '-1’000.000000 usdt' in summary and '33’000.00 бат' in summary
    assert reply.call_args.kwargs['parse_mode'] == 'HTML'
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == 1
    await telegram.session.close()


async def test_exchange_outbox_uses_old_cards_without_duplicate_or_technical_notice(finance):
    import asyncio

    from app.thai_telegram import ThaiTelegramDelivery
    from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository

    _, send = bot(finance)
    await send('/start', chat_name='Клиент <А>')
    card = await send('/пт 1000 обезнал 1000*33 <комментарий>', message_id=100,
        chat_name='Клиент <А>', actor_name='Автор <Б>')
    event = await OutboxDeliveryRepository(finance['pool']).claim()
    telegram = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)))
    delivery = ThaiTelegramDelivery(finance['app'], telegram, operations_chat_id=-999)
    await asyncio.gather(delivery.deliver(event), delivery.deliver(event))
    assert telegram.send_message.await_count == 2
    calls = {call.args[0]: call for call in telegram.send_message.call_args_list}
    assert calls[-123].args[1] == card
    request_card = calls[-999].args[1]
    assert '<b>Номер заявки</b>: <code>1</code>' in request_card
    assert '<b>Клиент</b>: <b>Клиент &lt;А&gt;</b>' in request_card
    assert '<b>Курс</b>: <code>33</code>' in request_card
    assert '<code>33’000.00 безнал</code>' in request_card
    assert '<b>Формула</b>: <code>1000*33</code>' in request_card
    assert '<b>Создал</b>: <b>Автор &lt;Б&gt;</b>' in request_card
    assert all(call.kwargs['parse_mode'] == 'HTML' for call in calls.values())
    assert all('Thai CRM' not in call.args[1] and 'fx_trade' not in call.args[1] for call in calls.values())
    assert await finance['pool'].fetchval('SELECT count(*) FROM telegram_deliveries') == 2


async def test_old_exchange_without_presentation_has_client_and_operations_cards(finance):
    from app.thai_telegram import ThaiTelegramDelivery
    from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository

    _, send = bot(finance)
    await send('/start', chat_name='Старый клиент')
    client_id = await finance['app'].trading_queries.client_by_chat(-123)
    created = await finance['app'].trading.create(key='tg:-123:100:draft', actor_id=finance['actor'],
        client_id=client_id, mode='wallet', receive={'instrument':'USDT', 'quantity':'1000'},
        pay={'instrument':'THB_CASH', 'quantity':'33000'}, comment='Ранее созданная заявка',
        external_reference='tg:-123:100')
    await finance['app'].trading.ready(key='tg:-123:100:ready', actor_id=finance['actor'], deal_id=created['deal_id'])
    await finance['app'].trading.post(key='tg:-123:100', actor_id=finance['actor'],
        deal_id=created['deal_id'], business_date=INSTANT.date(), effective_at=INSTANT)
    assert '<code>33</code>' in await send('/пт 1000 обат 1000*33 Ранее созданная заявка',
        message_id=100, chat_name='Старый клиент', actor_name='Новое имя автора')
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == 1
    assert 'presentation' not in (await finance['app'].trading_queries.detail(created['deal_id']))['attributes']
    event = await OutboxDeliveryRepository(finance['pool']).claim()
    telegram = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)))
    await ThaiTelegramDelivery(finance['app'], telegram, -999).deliver(event)
    assert telegram.send_message.await_count == 2
    for call in telegram.send_message.call_args_list:
        assert '<b>Курс</b>: <code>33</code>' in call.args[1]
        assert '<b>Отдаём</b>: <code>33’000.00 бат</code>' in call.args[1]
    request = telegram.send_message.call_args_list[0].args[1]
    assert '<b>Клиент</b>: <b>Старый клиент</b>' in request


async def test_exchange_delivery_retries_failed_chat_without_repeating_client_card(finance):
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import SendMessage

    from app.thai_telegram import ThaiTelegramDelivery
    from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository
    from services.finance.outbox import DeliveryError

    _, send = bot(finance)
    await send('/start', chat_name='Клиент')
    await send('/пт 1000 обат 33000', message_id=100)
    event = await OutboxDeliveryRepository(finance['pool']).claim()
    delivered = []
    failed = False

    async def send_message(chat_id, text, **kwargs):
        nonlocal failed
        if chat_id == 999 and not failed:
            failed = True
            raise TelegramNetworkError(method=SendMessage(chat_id=chat_id, text=text), message='network')
        delivered.append(chat_id)
        return SimpleNamespace(message_id=900)

    delivery = ThaiTelegramDelivery(finance['app'], SimpleNamespace(send_message=send_message), 999)
    with pytest.raises(DeliveryError):
        await delivery.deliver(event)
    assert delivered == [-123]
    await delivery.deliver(event)
    assert delivered == [-123, 999]
    assert await finance['pool'].fetchval('SELECT count(*) FROM telegram_deliveries') == 2
