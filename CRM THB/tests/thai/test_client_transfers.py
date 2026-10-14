import asyncio
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from db_asyncpg.thai_trading.workspace import DealWorkspaceQueries
from domain.finance.models import IdempotencyConflict, InvalidPosting
from tests.thai.test_expenses import common


async def clients(finance):
    return [
        (
            await finance["app"].clients.create_client(
                key=f"client:{i}", actor_id=finance["actor"], name=f"Client {i}"
            )
        )["client_id"]
        for i in range(2)
    ]


async def wallets(finance, client, instrument):
    rows = await finance["app"].client_queries.wallets(client)
    row = next(r for r in rows if r["instrument_code"] == instrument)
    return row["balance"], row["carrying_usdt"]


async def test_client_transfer_preserves_cash_and_carrying_allows_negative_and_storno(finance):
    source, target = await clients(finance)
    await finance["app"].wallets.deposit(
        **common(finance, "deposit"),
        client_id=source,
        instrument="THB_CASH",
        quantity="350",
        quoted_rate="35",
    )
    args = {
        **common(finance, "transfer"),
        "source_client_id": source,
        "target_client_id": target,
        "instrument": "THB_CASH",
        "quantity": "700",
        "quoted_rate": "70",
    }
    operation = await finance["app"].wallet_transfers.transfer(**args)
    assert await finance["app"].wallet_transfers.transfer(**args) == operation
    assert await wallets(finance, source, "THB_CASH") == (-350, -5)
    assert await wallets(finance, target, "THB_CASH") == (700, 15)
    cash = await finance["pool"].fetchrow(
        "SELECT balance,carrying_usdt FROM posted_account_balances WHERE account_id=$1",
        finance["cash"]["THB_CASH"],
    )
    assert tuple(cash) == (350, 10)
    with pytest.raises(IdempotencyConflict):
        await finance["app"].wallet_transfers.transfer(**{**args, "quantity": "701"})
    await finance["app"].reversals.reverse(
        **common(finance, "reverse"), original_id=operation.id, reason="Test"
    )
    assert await wallets(finance, source, "THB_CASH") == (350, 10)
    assert await wallets(finance, target, "THB_CASH") == (0, 0)


async def test_client_transfer_concurrency_and_workspace_pagination(finance):
    source, target = await clients(finance)

    async def send(i):
        await finance["app"].wallet_transfers.transfer(
            **common(finance, f"transfer:{i}"),
            source_client_id=source if i % 2 else target,
            target_client_id=target if i % 2 else source,
            instrument="USDT",
            quantity="1.123456",
        )

    await asyncio.gather(*(send(i) for i in range(8)))
    assert await wallets(finance, source, "USDT") == (0, 0)
    assert await wallets(finance, target, "USDT") == (0, 0)
    query = DealWorkspaceQueries(finance["pool"])
    first = await query.workspace(kind="client_transfer", limit=3)
    second = await query.workspace(kind="client_transfer", limit=3, before=first["next_cursor"])
    assert first["summaries"] == [{"status": "posted", "count": 8}]
    assert all(r["attributes"]["target_name"] in ("Client 0", "Client 1") for r in first["items"])
    assert len(first["items"]) == len(second["items"]) == 3
    assert not {r["entry_id"] for r in first["items"]} & {r["entry_id"] for r in second["items"]}
    assert (await query.workspace(query="absent"))["items"] == []
    with pytest.raises(InvalidPosting):
        await query.workspace(before="invalid")


async def test_client_transfer_api_validation_and_auth(finance):
    source, target = await clients(finance)
    app = create_thai_finance_api(container=finance["app"], jwt_secret="x" * 32)
    token = create_access_token(tg_user_id=12345, secret="x" * 32, ttl_seconds=60)
    data = {
        **common(finance, "api"),
        "source_client_id": source,
        "target_client_id": target,
        "instrument": "USDT",
        "quantity": "1.000001",
    }
    data["idempotency_key"] = data.pop("key")
    data.pop("actor_id")
    data["business_date"] = data["business_date"].isoformat()
    data["effective_at"] = data["effective_at"].isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.post("/api/v1/clients/transfers", json=data)).status_code == 401
        http.headers["Authorization"] = "Bearer " + token
        assert (await http.post("/api/v1/clients/transfers", json=data)).status_code == 200
        invalid = {**data, "idempotency_key": "invalid", "target_client_id": source}
        assert (await http.post("/api/v1/clients/transfers", json=invalid)).status_code == 422
        report = (await http.get("/api/v1/deals/workspace")).json()
        assert report["items"][0]["kind"] == "client_transfer"
        assert report["summaries"][0]["count"] == 1
        assert await wallets(finance, source, "USDT") == (
            -Decimal("1.000001"),
            -Decimal("1.000001"),
        )


async def test_best_change_preview_uses_posting_formula_without_writes(finance):
    from dataclasses import asdict

    from domain.accounting_policy import calculate_best_change

    app = create_thai_finance_api(container=finance["app"], jwt_secret="x" * 32)
    data = {"operation": "sale", "quantity": "100.123456", "market_rate": "35", "client_rate": "36"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.post("/api/v1/best-change/preview", json=data)).status_code == 401
        token = create_access_token(tg_user_id=12345, secret="x" * 32, ttl_seconds=60)
        http.headers["Authorization"] = "Bearer " + token
        result = await http.post("/api/v1/best-change/preview", json=data)
        assert result.status_code == 200
        expected = asdict(calculate_best_change(**data))
        assert result.json() == {
            k: str(v) if isinstance(v, Decimal) else v for k, v in expected.items()
        }
        assert (
            await http.post("/api/v1/best-change/preview", json={**data, "market_rate": "0"})
        ).status_code == 422
        assert (
            await http.post("/api/v1/best-change/preview", json={**data, "quantity": 100})
        ).status_code == 422
        assert await finance["pool"].fetchval("SELECT count(*) FROM financial_operations") == 0
        assert await finance["pool"].fetchval("SELECT count(*) FROM deals") == 0


async def chat_clients(finance):
    result=[]
    for i,name in enumerate(('Отправитель','Получатель','Получатель')):
        row=await finance['app'].clients.create_client(key=f'chat-client:{i}',actor_id=finance['actor'],
            name=name,chat_id=-200-i)
        result.append(row['client_id'])
    return result


async def prepare_transfer(finance, *, key='transfer-request',instrument='USDT',quantity='10'):
    source,target,other=await chat_clients(finance)
    result=await finance['app'].client_transfers.prepare_accounting(key=key,actor_id=finance['actor'],
        source_client_id=source,source_chat_id=-200,instrument=instrument,quantity=quantity,
        recipient_name='Получатель',effective_at=common(finance,key)['effective_at'])
    return result,source,target,other


async def resolve_transfer(finance,key,target,*,confirmed=True):
    return await finance['app'].client_transfers.resolve_accounting(**common(finance,key),
        recipient_id=target,confirmed=confirmed)


async def test_accounting_transfer_all_instruments_confirm_once_without_cash(finance):
    from tests.thai.test_posting import INSTANT
    source,target,_=await chat_clients(finance)
    for code,rate in (('THB_CASH','35'),('THB_BANK','36'),('USDT','1'),('USD_WH','1.02'),
                      ('USD_BL','1'),('EUR','1.1'),('EUR500','1.2'),('RUB','0.01')):
        await finance['app'].trading.set_rate(**common(finance,f'rate:{code}'),instrument=code,quoted_rate=rate)
        request=await finance['app'].client_transfers.prepare_accounting(key=f'request:{code}',
            actor_id=finance['actor'],source_client_id=source,source_chat_id=-200,instrument=code,
            quantity='10.123456' if code=='USDT' else '10.12',recipient_name='Получатель',effective_at=INSTANT)
        assert len(request['candidates'])==2
        assert (await wallets(finance,source,code))[0]==0
        results=await asyncio.gather(*(resolve_transfer(finance,f'request:{code}',target) for _ in range(3)))
        assert results[0]==results[1]==results[2]
        quantity=Decimal(request['quantity'])
        assert (await wallets(finance,source,code))[0]==-quantity
        assert (await wallets(finance,target,code))[0]==quantity
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind='wallet_transfer'")==8
    assert all(r['balance']==0 for r in await finance['app'].queries.cash_balances())


async def test_transfer_reject_and_candidates_survive_restart_and_reject_forgery(finance):
    from app.container import ApplicationContainer
    from domain.finance.models import PermissionDenied
    request,source,target,other=await prepare_transfer(finance)
    finance['app']=ApplicationContainer.build(finance['pool'])
    assert await finance['app'].client_queries.transfer_request('transfer-request')==request
    with pytest.raises(InvalidPosting,match='не принадлежит'):
        await resolve_transfer(finance,'transfer-request',source)
    user=await finance['pool'].fetchval("INSERT INTO users(display_name) VALUES ('Reader') RETURNING id")
    with pytest.raises(PermissionDenied):
        await finance['app'].client_transfers.resolve_accounting(**{**common(finance,'transfer-request'),'actor_id':user},
            recipient_id=target,confirmed=True)
    assert (await resolve_transfer(finance,'transfer-request',target,confirmed=False))['cancelled']
    with pytest.raises(IdempotencyConflict):
        await resolve_transfer(finance,'transfer-request',target)
    assert (await wallets(finance,source,'USDT'))==(0,0)
    # Choosing two candidates concurrently still resolves one request once.
    await finance['app'].client_transfers.prepare_accounting(key='race',actor_id=finance['actor'],
        source_client_id=source,source_chat_id=-200,instrument='USDT',quantity='10',
        recipient_name='Получатель',effective_at=common(finance,'race')['effective_at'])
    results=await asyncio.gather(resolve_transfer(finance,'race',target),resolve_transfer(finance,'race',other),return_exceptions=True)
    assert sum(isinstance(r,dict) for r in results)==1
    assert sum(isinstance(r,IdempotencyConflict) for r in results)==1
    assert (await wallets(finance,source,'USDT'))==(-10,-10)


async def test_transfer_correction_and_cancel_are_atomic_and_keep_root(finance,monkeypatch):
    from unittest.mock import AsyncMock
    _,source,target,_=await prepare_transfer(finance)
    posted=await resolve_transfer(finance,'transfer-request',target)
    operation=posted['operation_id']
    transfer=finance['app'].wallet_transfers.transfer
    monkeypatch.setattr(finance['app'].wallet_transfers,'transfer',AsyncMock(side_effect=InvalidPosting('replacement failed')))
    args={**common(finance,'adjust'),'operation_id':operation,'new_amount':'15','source_chat_id':-200}
    with pytest.raises(InvalidPosting,match='replacement failed'):
        await finance['app'].client_transfers.adjust_accounting(**args)
    assert (await wallets(finance,source,'USDT'))==(-10,-10)
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind='reversal'")==0
    monkeypatch.setattr(finance['app'].wallet_transfers,'transfer',transfer)
    with pytest.raises(InvalidPosting,match='отправителя'):
        await finance['app'].client_transfers.adjust_accounting(**{**args,'source_chat_id':-201})
    corrected=await finance['app'].client_transfers.adjust_accounting(**args)
    assert await finance['app'].client_transfers.adjust_accounting(**args)==corrected
    assert corrected['root_id']==operation and corrected['operation_id']!=operation
    assert (await wallets(finance,source,'USDT'))==(-15,-15)
    assert (await wallets(finance,target,'USDT'))==(15,15)
    cancelled=await finance['app'].client_transfers.adjust_accounting(**common(finance,'cancel'),operation_id=operation,
        new_amount=None,source_chat_id=-200)
    assert cancelled['cancelled']
    assert (await wallets(finance,source,'USDT'))==(0,0)
    assert (await wallets(finance,target,'USDT'))==(0,0)
    assert (await finance['app'].client_queries.transfer_context(operation))['cancelled']


async def test_transfer_correction_rejects_precision_and_later_wallet_movements(finance):
    from domain.finance.models import ReversalBlocked
    _,source,target,_=await prepare_transfer(finance)
    operation=(await resolve_transfer(finance,'transfer-request',target))['operation_id']
    with pytest.raises(InvalidPosting,match='точность'):
        await finance['app'].client_transfers.adjust_accounting(**common(finance,'precision'),operation_id=operation,
            new_amount='1.1234567',source_chat_id=-200)
    await finance['app'].wallet_transfers.transfer(**common(finance,'later'),source_client_id=target,
        target_client_id=source,instrument='USDT',quantity='1')
    with pytest.raises(ReversalBlocked,match='последующие'):
        await finance['app'].client_transfers.adjust_accounting(**common(finance,'cancel'),operation_id=operation,
            new_amount=None,source_chat_id=-200)
    assert (await wallets(finance,source,'USDT'))==(-9,-9)


async def test_transfer_receipts_retry_failed_send_and_old_events_render_current_version(finance):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import SendMessage

    from app.thai_telegram import ThaiTelegramDelivery
    from services.finance.outbox import DeliveryError, OutboxEvent
    _,source,target,_=await prepare_transfer(finance)
    operation=(await resolve_transfer(finance,'transfer-request',target))['operation_id']
    serial=900
    async def sent(*args,**kwargs):
        nonlocal serial
        serial+=1
        return SimpleNamespace(message_id=serial)
    telegram=SimpleNamespace(send_photo=AsyncMock(side_effect=sent),send_message=AsyncMock(side_effect=[
        TelegramNetworkError(method=SendMessage(chat_id=-200,text='x'),message='offline')]))
    delivery=ThaiTelegramDelivery(finance['app'],telegram)
    with pytest.raises(DeliveryError):
        await delivery.deliver_client_transfer(operation)
    assert telegram.send_photo.await_count==1
    assert await finance['app'].client_queries.transfer_by_message(-200,901)==operation
    telegram.send_message.side_effect=sent
    await delivery.deliver_client_transfer(operation)
    await delivery.deliver_client_transfer(operation)
    assert telegram.send_photo.await_count==2 and telegram.send_message.await_count==3
    await finance['app'].client_transfers.adjust_accounting(**common(finance,'edit'),operation_id=operation,new_amount='20')
    await delivery.deliver(OutboxEvent(1,'old','operation.posted','{"operation_id":'+str(operation)+'}','lease'))
    assert telegram.send_photo.await_count==4
    assert '20.000000' in telegram.send_message.call_args.args[1]
    await finance['app'].client_transfers.adjust_accounting(**common(finance,'cancel'),operation_id=operation,new_amount=None)
    await delivery.deliver_client_transfer(operation)
    assert 'отменён' in telegram.send_message.call_args.args[1]


async def test_transfer_router_selects_confirms_and_supports_receipt_reply(finance,monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, User

    from app.setup_handlers import create_router
    from app.thai_telegram import ThaiTelegramCommands
    from tests.thai.test_posting import INSTANT
    source,target,other=await chat_clients(finance)
    commands=ThaiTelegramCommands(finance['app'],finance['config'])
    telegram=Bot('123456:TEST')
    user=User(id=12345,is_bot=False,first_name='Manager')
    answers=AsyncMock(return_value=SimpleNamespace(message_id=999))
    photos=AsyncMock(return_value=SimpleNamespace(message_id=910))
    monkeypatch.setattr(telegram,'send_message',answers)
    monkeypatch.setattr(telegram,'send_photo',photos)
    monkeypatch.setattr(telegram,'edit_message_text',AsyncMock())
    monkeypatch.setattr(telegram,'answer_callback_query',AsyncMock())
    monkeypatch.setattr(Message,'answer',answers)
    monkeypatch.setattr(Message,'edit_text',AsyncMock())
    monkeypatch.setattr(CallbackQuery,'answer',AsyncMock())
    router=create_router(commands)
    chat=Chat(id=-200,type='supergroup',title='Отправитель')
    message=Message(message_id=100,date=INSTANT,chat=chat,from_user=user,text='/перевод 1,125 USDT Получатель').as_(telegram)
    try:
        await router.message.handlers[0].callback(message)
        markup=answers.call_args.kwargs['reply_markup']
        assert len(markup.inline_keyboard)==2
        assert (await wallets(finance,source,'USDT'))==(0,0)
        card=Message(message_id=999,date=INSTANT,chat=chat).as_(telegram)
        callback=CallbackQuery(id='pick',from_user=user,message=card,chat_instance='x',
            data=markup.inline_keyboard[0][0].callback_data).as_(telegram)
        await router.callback_query.handlers[-1].callback(callback)
        confirm=answers.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
        assert confirm.startswith('ct:confirm:')
        assert (await wallets(finance,source,'USDT'))==(0,0)
        await router.callback_query.handlers[-1].callback(callback.model_copy(update={'data':confirm}).as_(telegram))
        assert (await wallets(finance,source,'USDT'))==(-Decimal('1.125'),-Decimal('1.125'))
        assert photos.await_count==2
        receipt=Message(message_id=910,date=INSTANT,chat=chat,from_user=User(id=telegram.id,is_bot=True,first_name='Bot'))
        edit=message.model_copy(update={'message_id':101,'text':'/перевод 2','reply_to_message':receipt}).as_(telegram)
        # New sends need unique message IDs in each chat.
        photos.return_value=SimpleNamespace(message_id=911)
        answers.return_value=SimpleNamespace(message_id=1000)
        await router.message.handlers[0].callback(edit)
        assert (await wallets(finance,source,'USDT'))==(-2,-2)
        photos.return_value=SimpleNamespace(message_id=912)
        answers.return_value=SimpleNamespace(message_id=1001)
        await router.message.handlers[0].callback(edit.model_copy(update={'message_id':102,'text':'/отмена'}).as_(telegram))
        assert (await wallets(finance,source,'USDT'))==(0,0)
    finally:
        await telegram.session.close()


async def test_transfer_closed_period_and_crm_adjustment_authorization(finance):
    from domain.finance.models import PeriodClosed
    _,source,target,_=await prepare_transfer(finance)
    operation=(await resolve_transfer(finance,'transfer-request',target))['operation_id']
    api=create_thai_finance_api(container=finance['app'],jwt_secret='x'*32)
    data={'idempotency_key':'api-adjust','new_amount':'12',
          'effective_at':common(finance,'x')['effective_at'].isoformat()}
    token=create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as http:
        assert (await http.post(f'/api/v1/clients/transfers/{operation}/adjust',json=data)).status_code==401
        http.headers['Authorization']='Bearer '+token
        result=await http.post(f'/api/v1/clients/transfers/{operation}/adjust',json=data)
        assert result.status_code==200,result.text
        assert (await http.get(f'/api/v1/clients/transfers/{operation}')).json()['root_id']==operation
    await finance['pool'].execute("INSERT INTO period_locks(context,period) VALUES ('ledger','2026-10-01')")
    with pytest.raises(PeriodClosed):
        await finance['app'].client_transfers.adjust_accounting(**common(finance,'closed'),operation_id=operation,new_amount=None)
    assert (await wallets(finance,source,'USDT'))==(-12,-12)
    assert (await wallets(finance,target,'USDT'))==(12,12)
