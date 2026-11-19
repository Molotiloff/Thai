"""Cancelling an old exchange must equal a history where it never occurred."""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from domain.finance.models import IdempotencyConflict, InvalidPosting, PeriodClosed, ReversalBlocked
from tests.thai.test_exchange_history import (
    assert_consistent,
    client,
    common,
    exchange,
    total_profit,
    wallet_state,
)
from tests.thai.test_posting import DAY, INSTANT


async def scenario(finance, *, include_root):
    first, second = await client(finance,'first'), await client(finance,'second')
    root = await exchange(finance,'root',first,('THB_CASH','3500'),('USDT','100')) if include_root else None
    later = await exchange(finance,'later',first,('USDT','50'),('THB_CASH','1600'))
    await finance['app'].wallet_transfers.transfer(**common(finance,'transfer'),source_client_id=first,
        target_client_id=second,instrument='THB_CASH',quantity='700')
    await exchange(finance,'recipient-trade',second,('THB_CASH','300'),('USDT','10'))
    await finance['app'].conversions.transfer(**common(finance,'convert'),client_id=second,
        source_instrument='THB_CASH',target_instrument='THB_BANK',quantity='100',fee='5',quoted_rate=None)
    return root,[first,second],later


async def test_cancel_chain_profit_equals_history_without_exchange(finance,reference_finance):
    _, reference_clients, _ = await scenario(reference_finance,include_root=False)
    root, clients, later = await scenario(finance,include_root=True)
    outbox_before = await finance['pool'].fetchval('SELECT count(*) FROM telegram_outbox')
    result = await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,
        expected_deal_id=root,reason='Remove historical exchange')
    assert len(result['recalculated_operations']) == 4
    assert await wallet_state(finance,clients) == await wallet_state(reference_finance,reference_clients)
    assert await total_profit(finance) == await total_profit(reference_finance)
    assert (await finance['app'].trading_queries.detail(root))['effective_status'] == 'cancelled'
    assert (await finance['app'].trading_queries.exchange_context(later))['active']['effective_status'] == 'posted'
    assert await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,
        expected_deal_id=root,reason='Remove historical exchange') == result
    assert await finance['pool'].fetchval('SELECT count(*) FROM telegram_outbox') == outbox_before+3
    # One root reversal and two card refreshes; no financial notices for technical replay.
    assert await finance['pool'].fetchval("SELECT count(*) FROM telegram_outbox WHERE event_type='exchange.revalued'") == 2
    await assert_consistent(finance)


async def test_cancel_uses_original_date_and_retry_survives_new_timestamp(finance):
    cid = await client(finance,'client')
    root = await exchange(finance,'root',cid,('THB_CASH','3500'),('USDT','100'))
    now = INSTANT+timedelta(days=40)
    result = await finance['app'].trading.cancel(key='cancel',actor_id=finance['actor'],deal_id=root,
        business_date=now.date(),effective_at=now,reason='Remove old exchange')
    operation = await finance['pool'].fetchrow('SELECT business_date,effective_at FROM financial_operations WHERE id=$1',result['id'])
    assert operation['business_date'] == DAY and operation['effective_at'] == INSTANT
    assert await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,reason='Remove old exchange') == result
    with pytest.raises(IdempotencyConflict):
        await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,reason='Different reason')


async def test_cancel_failure_rolls_back_whole_history(finance,monkeypatch):
    root, clients, _ = await scenario(finance,include_root=True)
    before_wallets = await wallet_state(finance,clients)
    before_profit = await total_profit(finance)
    before_operations = await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')
    before_outbox = await finance['pool'].fetchval('SELECT count(*) FROM telegram_outbox')
    monkeypatch.setattr(finance['app'].trading,'post',AsyncMock(side_effect=InvalidPosting('replay failed')))
    with pytest.raises(InvalidPosting,match='replay failed'):
        await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,reason='Test rollback')
    assert await wallet_state(finance,clients) == before_wallets
    assert await total_profit(finance) == before_profit
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == before_operations
    assert await finance['pool'].fetchval('SELECT count(*) FROM telegram_outbox') == before_outbox
    assert (await finance['app'].trading_queries.detail(root))['effective_status'] == 'posted'


async def test_cancel_removes_dependency_on_missing_original(finance):
    cid = await client(finance,'client')
    root = await exchange(finance,'root',cid,('THB_CASH','3500'),('USDT','100'))
    source = (await finance['app'].trading_queries.detail(root))['operation_id']
    later = await finance['app'].trading.create(key='later',actor_id=finance['actor'],client_id=cid,mode='wallet',
        receive={'instrument':'USDT','quantity':'50'},pay={'instrument':'THB_CASH','quantity':'1600'})
    await finance['app'].trading.ready(key='ready',actor_id=finance['actor'],deal_id=later['deal_id'])
    await finance['app'].trading.post(**common(finance,'post'),deal_id=later['deal_id'],dependency_operation_ids=(source,))
    result = await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,reason='Remove root')
    assert not await finance['pool'].fetchval('SELECT EXISTS(SELECT 1 FROM financial_operation_dependencies WHERE dependent_operation_id=$1 AND source_operation_id=$2)',
        result['recalculated_operations'][0]['operation_id'],source)
    assert await total_profit(finance) == 0
    await assert_consistent(finance)


async def test_cancel_rejects_stale_version_and_closed_original_month(finance):
    cid = await client(finance,'client')
    root = await exchange(finance,'root',cid,('THB_CASH','3500'),('USDT','100'))
    with pytest.raises(InvalidPosting,match='Заявка изменена'):
        await finance['app'].trading.cancel(**common(finance,'stale'),deal_id=root,
            expected_deal_id=root+99,reason='Stale form')
    await finance['pool'].execute("INSERT INTO period_locks(context,period) VALUES ('ledger',$1)",DAY.replace(day=1))
    with pytest.raises(PeriodClosed):
        await finance['app'].trading.cancel(**common(finance,'closed'),deal_id=root,reason='Closed month')
    assert (await finance['app'].trading_queries.detail(root))['effective_status'] == 'posted'


async def test_cancel_api_recalculates_old_wallet_exchange(finance,reference_finance):
    root, clients, _ = await scenario(finance,include_root=True)
    _, reference_clients, _ = await scenario(reference_finance,include_root=False)
    api = create_thai_finance_api(container=finance['app'],jwt_secret='x'*32)
    headers={'Authorization':'Bearer '+create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)}
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test',headers=headers) as http:
        data={'idempotency_key':'api-cancel','effective_at':INSTANT.isoformat(),
              'expected_deal_id':root,'reason':'Cancel old exchange'}
        response=await http.post(f'/api/v1/deals/{root}/cancel',json=data)
        assert response.status_code == 200,response.text
        assert (await http.post(f'/api/v1/deals/{root}/cancel',json=data)).json() == response.json()
    assert await wallet_state(finance,clients) == await wallet_state(reference_finance,reference_clients)
    assert await total_profit(finance) == await total_profit(reference_finance)


async def test_cancel_removes_counterparty_accrual_and_replays_spend(finance,reference_finance):
    from tests.thai.test_counterparty import create, setup_clients

    async def run(db,include_root):
        buyer,kt = await setup_clients(db)
        recipient = await client(db,'recipient')
        root = await create(db,buyer,kt) if include_root else None
        await db['app'].wallet_transfers.transfer(**common(db,'spend'),source_client_id=kt,
            target_client_id=recipient,instrument='USDT',quantity='2')
        await create(db,buyer,kt,key='next-exchange',spread='0.10')
        return root,[buyer,kt,recipient]
    root,clients = await run(finance,True)
    _,reference = await run(reference_finance,False)
    result = await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,reason='Remove KT deal')
    assert len(result['recalculated_operations']) == 2
    assert await wallet_state(finance,clients) == await wallet_state(reference_finance,reference)
    assert await total_profit(finance) == await total_profit(reference_finance)
    await assert_consistent(finance)


async def test_telegram_button_cancels_old_exchange_and_updates_same_card(finance):
    from tests.thai.test_exchange_workflow import setup
    commands,bot,delivery,_ = await setup(finance)
    cid = await finance['app'].trading_queries.client_by_chat(-123)
    later = await exchange(finance,'later',cid,('THB_CASH','1600'),('USDT','50'))
    await commands.cancel_exchange(telegram_user_id=12345,chat_id=-123,message_id=900,data='req_cancel:1:1')
    count = await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')
    await commands.cancel_exchange(telegram_user_id=12345,chat_id=-123,message_id=900,data='req_cancel:1:1')
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == count
    await delivery.deliver_exchange_request(1)
    assert bot.edit_message_text.await_count == 2
    assert all(call.kwargs.get('reply_markup') is None for call in bot.edit_message_text.call_args_list)
    assert (await finance['app'].trading_queries.detail(1))['effective_status'] == 'cancelled'
    assert (await finance['app'].trading_queries.exchange_context(later))['active']['effective_status'] == 'posted'
    await assert_consistent(finance)


async def test_cancel_can_remove_corrected_version_through_stable_root(finance):
    cid = await client(finance,'client')
    root = await exchange(finance,'root',cid,('THB_CASH','3500'),('USDT','100'))
    await exchange(finance,'later',cid,('USDT','50'),('THB_CASH','1600'))
    corrected = await finance['app'].trading.correct(**common(finance,'correct'),deal_id=root,reason='Correct root',
        replacement={'mode':'wallet','receive':{'instrument':'THB_CASH','quantity':'3300'},
            'pay':{'instrument':'USDT','quantity':'100'}})
    with pytest.raises(InvalidPosting,match='Заявка изменена'):
        await finance['app'].trading.cancel(**common(finance,'stale'),deal_id=root,reason='Stale version',expected_deal_id=root)
    result = await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,
        expected_deal_id=corrected['deal_id'],reason='Cancel current version')
    assert result['deal_id'] == corrected['deal_id']
    assert (await finance['app'].trading_queries.exchange_context(root))['active']['effective_status'] == 'cancelled'
    assert await total_profit(finance) == 0
    await assert_consistent(finance)


async def test_cancel_last_quote_restores_deferred_valuation(finance,reference_finance):
    async def run(db,include_root):
        cid = await client(db,'client')
        await db['app'].wallets.deposit(**common(db,'deposit'),client_id=cid,instrument='THB_CASH',quantity='1000')
        root = await exchange(db,'root',cid,('USDT','100'),('THB_CASH','3300')) if include_root else None
        await db['app'].wallets.withdraw(**common(db,'withdraw'),client_id=cid,instrument='THB_CASH',quantity='500')
        return cid,root
    cid,root = await run(finance,True)
    reference,_ = await run(reference_finance,False)
    await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,reason='Remove last quote')
    assert await wallet_state(finance,[cid]) == await wallet_state(reference_finance,[reference])
    cash='SELECT instrument_code,quantity,cost_usdt,unvalued_quantity FROM inventory_balances ORDER BY instrument_code'
    assert await finance['pool'].fetch(cash) == await reference_finance['pool'].fetch(cash)
    assert await total_profit(finance) == await total_profit(reference_finance)
    await assert_consistent(finance)


async def test_cancel_does_not_replay_client_closure_document(finance):
    cid = await client(finance,'client')
    root = await exchange(finance,'root',cid,('THB_CASH','3500'),('USDT','100'))
    await finance['app'].wallet_adjustments.prepare_zero(key='zero',actor_id=finance['actor'],client_id=cid)
    await finance['app'].wallet_adjustments.resolve_zero(**common(finance,'zero'),confirmed=True)
    before = await wallet_state(finance,[cid])
    with pytest.raises(ReversalBlocked,match='отдельная ревизия'):
        await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,reason='Business blocker')
    assert await wallet_state(finance,[cid]) == before
    assert (await finance['app'].trading_queries.detail(root))['effective_status'] == 'posted'
