import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from app.thai_russian_commands import SUFFIXES
from app.thai_telegram import ThaiTelegramCommands, ThaiTelegramDelivery
from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository
from db_asyncpg.thai_trading.workspace import DealWorkspaceQueries
from domain.finance.models import InsufficientCash, InvalidPosting
from tests.thai.test_posting import INSTANT, balance


async def send(commands, text, message_id, **kwargs):
    return await commands.execute(telegram_user_id=12345, chat_id=-123, message_id=message_id,
        text=text, effective_at=INSTANT, chat_name='Клиент <А>', actor_name='Менеджер <Б>', **kwargs)


@pytest.mark.parametrize('suffix,code', SUFFIXES.items())
async def test_cash_request_full_cycle_all_instruments(finance, suffix, code):
    commands = ThaiTelegramCommands(finance['app'], finance['config'])
    for sequence,prefix,quantity in ((100,'деп','200.25'),(200,'выд','50.12')):
        before = await balance(finance, code)
        card = await send(commands, f'/{prefix}{suffix} (200+0.25)' if prefix=='деп' else f'/{prefix}{suffix} {quantity}', sequence)
        assert 'Заявка на' in card and 'Город' not in card and 'thai' not in card.lower()
        assert await balance(finance,code)==before
        deal_id = await commands.cash_request_by_command(-123, sequence)
        request_id = f'Б-{deal_id:06d}'
        deal = await finance['app'].trading_queries.detail(deal_id)
        assert deal['effective_status']=='draft' and deal['operation_id'] is None
        signed = quantity if prefix=='деп' else '-'+quantity
        with pytest.raises(InvalidPosting, match='готовой'):
            await send(commands, f'/{code} {signed} {request_id}', sequence+1)
        await finance['app'].cash_requests.change_request_status(key=f'ready-{sequence}', actor_id=finance['actor'],
            request_id=request_id, action='ready')
        assert await balance(finance,code)==before
        result = await send(commands, f'/{code} {signed} {request_id}', sequence+2)
        assert 'проведена: операция' in result
        assert await send(commands, f'/{code} {signed} {request_id}', sequence+3)==result
        assert (await finance['app'].trading_queries.detail(deal_id))['effective_status']=='posted'
    assert (await balance(finance,code))[0] == Decimal('150.13')
    workspace = DealWorkspaceQueries(finance['pool'])
    for kind in ('deposit','withdrawal'):
        result = await workspace.workspace(kind=kind)
        assert len(result['items'])==1 and result['items'][0]['status']=='posted'
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind IN ('wallet_deposit','wallet_withdrawal')") == 2


async def test_cash_edit_cancel_and_stale_button_do_not_post(finance):
    commands = ThaiTelegramCommands(finance['app'], finance['config'])
    await send(commands, '/выдбат 100.25 @cashier +123456789 ! <комментарий>', 10)
    original = await finance['app'].trading_queries.detail(1)
    assert original['attributes']['quantity']=='100.25'
    assert original['attributes']['contact1']=='@cashier'
    assert await send(commands, '/выдбат 100.25 @cashier +123456789 ! <комментарий>', 10)
    assert await finance['pool'].fetchval('SELECT count(*) FROM deals') == 1
    await commands.edit_cash_request(telegram_user_id=12345, chat_id=-123, message_id=11,
        request_id=1, text='/выдбат 200.75 @other ! правка', actor_name='Редактор', effective_at=INSTANT)
    changed = await finance['app'].trading_queries.detail(1)
    assert changed['attributes']['revision']==2 and changed['attributes']['quantity']=='200.75'
    assert changed['attributes']['pin_code']==original['attributes']['pin_code']
    with pytest.raises(InvalidPosting, match='изменена'):
        await finance['app'].cash_requests.change_request_status(key='stale', actor_id=finance['actor'],
            request_id='Б-000001', action='ready', expected_revision=1)
    await finance['app'].cash_requests.change_request_status(key='cancel', actor_id=finance['actor'],
        request_id='Б-000001', action='cancel')
    with pytest.raises(InvalidPosting):
        await commands.edit_cash_request(telegram_user_id=12345, chat_id=-123, message_id=12,
            request_id=1, text='/выдбат 300', actor_name='Редактор', effective_at=INSTANT)
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == 0
    assert await balance(finance,'THB_CASH')==(0,0)


async def test_cash_delivery_has_old_buttons_and_updates_same_cards(finance):
    from dataclasses import replace
    config = replace(finance['config'], operations_chat_id=-999)
    commands = ThaiTelegramCommands(finance['app'], config)
    await send(commands, '/депбат 100.25 @cashier ! <комментарий>', 10)
    telegram = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)),
        edit_message_text=AsyncMock())
    delivery = ThaiTelegramDelivery(finance['app'],telegram,-999)
    event = await OutboxDeliveryRepository(finance['pool']).claim()
    await delivery.deliver(event)
    assert telegram.send_message.await_count == 2
    cards = {call.args[0]:call for call in telegram.send_message.call_args_list}
    assert cards[-123].kwargs['reply_markup'] is None
    assert '&lt;комментарий&gt;' in cards[-123].args[1]
    actions = cards[-999].kwargs['reply_markup'].inline_keyboard
    assert actions[0][0].text == 'Готово к расчету'
    await commands.cash_status(telegram_user_id=12345,chat_id=-999,message_id=900,data=actions[0][0].callback_data)
    await delivery.deliver_cash_request(1)
    assert telegram.send_message.await_count == 2
    assert telegram.edit_message_text.await_count == 2
    assert 'Готово к расчету' in telegram.edit_message_text.call_args_list[0].args[0]
    ready_actions = telegram.edit_message_text.call_args_list[0].kwargs['reply_markup']
    assert ready_actions.inline_keyboard[0][0].text == 'Сделка завершена'
    with pytest.raises(InvalidPosting, match='Сначала проведите'):
        await commands.cash_status(telegram_user_id=12345,chat_id=-999,message_id=900,
            data=ready_actions.inline_keyboard[0][0].callback_data)
    with pytest.raises(InvalidPosting, match='рабочей'):
        await commands.cash_status(telegram_user_id=12345,chat_id=-123,message_id=900,
            data=actions[0][0].callback_data)


async def test_cash_api_creation_ready_settlement_and_reversal_share_ledger(finance):
    commands = ThaiTelegramCommands(finance['app'], finance['config'])
    await send(commands, '/start', 1)
    client_id = await finance['app'].trading_queries.client_by_chat(-123)
    app = create_thai_finance_api(container=finance['app'], jwt_secret='x'*32)
    token = create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+token}) as http:
        result = await http.post('/api/v1/cash-requests',json={'idempotency_key':'api-create','client_id':client_id,
            'kind':'deposit','instrument':'THB_CASH','quantity':'1000.25','contact1':'@cashier'})
        assert result.status_code==200, result.text
        deal_id = result.json()['deal_id']
        assert (await http.get('/api/v1/deals/workspace?kind=deposit&status=draft')).json()['items'][0]['deal_id']==deal_id
        assert await balance(finance,'THB_CASH')==(0,0)

        assert (await http.post(f'/api/v1/deals/{deal_id}/ready',json={'idempotency_key':'api-ready'})).status_code==200
        result = await http.post(f'/api/v1/cash-requests/{deal_id}/settle',json={'idempotency_key':'api-settle',
            'effective_at':INSTANT.isoformat(),'instrument':'THB_CASH','signed_quantity':'1000.25'})
        assert result.status_code==200, result.text
        operation_id = result.json()['id']
        assert (await balance(finance,'THB_CASH'))[0]==Decimal('1000.25')
        result = await http.post(f'/api/v1/finance/operations/{operation_id}/reversal',json={
            'idempotency_key':'api-undo','effective_at':INSTANT.isoformat(),'reason':'Ошибочное внесение'})
        assert result.status_code==200, result.text
        assert (await http.get(f'/api/v1/deals/{deal_id}')).json()['effective_status']=='cancelled'
        assert len((await http.get('/api/v1/deals/workspace?kind=deposit&status=cancelled')).json()['items'])==1
        assert await balance(finance,'THB_CASH')==(0,0)


async def test_cash_mismatch_and_insufficient_cash_leave_request_ready(finance):
    commands = ThaiTelegramCommands(finance['app'], finance['config'])
    await send(commands, '/выдбат 1000', 1)
    await finance['app'].cash_requests.change_request_status(key='ready',actor_id=finance['actor'],
        request_id='Б-000001',action='ready')
    for command in ('/бат 1000 Б-000001', '/бат -999 Б-000001', '/USDT -1000 Б-000001'):
        with pytest.raises(InvalidPosting, match='не совпадают'):
            await send(commands, command, 2)
    with pytest.raises(InsufficientCash):
        await send(commands, '/бат -1000 Б-000001', 4)
    assert (await finance['app'].trading_queries.detail(1))['effective_status']=='ready'
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind='wallet_withdrawal'")==0
    assert await balance(finance,'THB_CASH')==(0,0)


async def test_concurrent_cash_card_delivery_updates_one_copy_per_chat(finance):
    commands = ThaiTelegramCommands(finance['app'], finance['config'])
    await send(commands, '/депт 1', 1)
    telegram = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)),
        edit_message_text=AsyncMock())
    delivery = ThaiTelegramDelivery(finance['app'],telegram,None)
    await asyncio.gather(delivery.deliver_cash_request(1),delivery.deliver_cash_request(1))
    assert telegram.send_message.await_count==1
    assert telegram.edit_message_text.await_count==1
    assert await finance['pool'].fetchval("SELECT count(*) FROM telegram_message_links WHERE deal_id=1")==1
