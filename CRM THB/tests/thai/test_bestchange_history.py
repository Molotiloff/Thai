"""BestChange terms survive historical cash repricing without duplicate payouts."""
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from domain.finance.models import PeriodClosed, ReversalBlocked
from tests.thai.test_exchange_history import (
    assert_consistent,
    client,
    common,
    exchange,
    total_profit,
    wallet_state,
)
from tests.thai.test_posting import DAY
from tests.thai.test_profit import configure


async def scenario(db,include_root=True,mode='cash',spend=False,client_rate='34'):
    _,dany = await configure(db)
    seed = await client(db,'seed')
    buyer = await client(db,'buyer')
    recipient = await client(db,'recipient')
    await exchange(db,'seed-rate',seed,('THB_CASH','3300'),('USDT','100'))
    root = await exchange(db,'root',buyer,('THB_CASH','3500'),('USDT','100')) if include_root else None
    await db['app'].manual_cash.adjust(**common(db,'cash-in'),instrument='THB_CASH',quantity='10000',chat_id=-999)
    posted = await db['app'].best_change.record(**common(db,'best'),operation='purchase',quantity='100',
        market_rate='35',client_rate=client_rate,mode=mode,client_id=buyer if mode=='wallet' else None)
    bc = await db['pool'].fetchval('SELECT id FROM deals WHERE operation_id=$1',posted.id)
    if spend:
        await db['app'].wallet_transfers.transfer(**common(db,'dany-spend'),source_client_id=dany,
            target_client_id=recipient,instrument='USDT',quantity='0.25')
    return root,bc,[seed,buyer,dany,recipient]


async def cash_state(db):
    return await db['pool'].fetch('SELECT instrument_code,quantity,cost_usdt,unvalued_quantity FROM inventory_balances ORDER BY instrument_code')


@pytest.mark.parametrize('mode', ['cash','wallet'])
async def test_cancel_reprices_bestchange_and_keeps_contract_and_accruals(finance,reference_finance,mode):
    root,bc,clients = await scenario(finance,mode=mode,spend=True)
    _,_,reference_clients = await scenario(reference_finance,include_root=False,mode=mode,spend=True)
    previous = (await finance['app'].profit_queries.best_change_context(bc))['active']
    result = await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,reason='Historical cancel')
    current = (await finance['app'].profit_queries.best_change_context(bc))['active']
    assert current['active'] and current['deal_id'] != previous['deal_id']
    assert current['attributes']['calculation'] == previous['attributes']['calculation']
    assert current['attributes']['receive'] == previous['attributes']['receive']
    assert current['attributes']['pay'] == previous['attributes']['pay']
    assert current['business_date'] == previous['business_date']
    assert current['effective_at'] == previous['effective_at']
    assert await wallet_state(finance,clients) == await wallet_state(reference_finance,reference_clients)
    assert await cash_state(finance) == await cash_state(reference_finance)
    assert await total_profit(finance) == await total_profit(reference_finance)
    assert await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,reason='Historical cancel') == result
    # Only one active business fee/accrual remains after exact reversal + replacement.
    active = await finance['app'].profit_queries.best_change_report(DAY.replace(day=1))
    assert active['count'] == 1 and active['daniyar_usdt'] == Decimal('1')
    await assert_consistent(finance)


async def test_correction_keeps_bestchange_prices_and_updates_existing_card_only(finance):
    from app.thai_telegram import ThaiTelegramDelivery
    from services.finance.outbox import OutboxEvent
    from tests.thai.telegram_stub import telegram_mock
    root,bc,_ = await scenario(finance)
    binding = await finance['app'].profit_queries.bindings()
    for n,name in enumerate(('best_change_buffer','daniyar_balance')):
        await finance['app'].clients.bind_chat(key='bind-'+name,actor_id=finance['actor'],
            client_id=binding[name]['client_id'],chat_id=-501-n)
    bot = telegram_mock()
    delivery = ThaiTelegramDelivery(finance['app'],bot)
    await delivery.deliver_best_change(bc)
    sent = bot.send_message.await_count
    before = (await finance['app'].profit_queries.best_change_context(bc))['active']['attributes']['calculation']
    await finance['app'].trading.correct(**common(finance,'correct'),deal_id=root,reason='Change old rate',
        replacement={'mode':'wallet','receive':{'instrument':'THB_CASH','quantity':'3600'},
            'pay':{'instrument':'USDT','quantity':'100'}})
    after = (await finance['app'].profit_queries.best_change_context(bc))['active']['attributes']['calculation']
    assert after == before
    event = await finance['pool'].fetchrow("SELECT * FROM telegram_outbox WHERE event_type='best_change.revalued'")
    # Route the refresh without running any real Telegram worker.
    await delivery.deliver(OutboxEvent(1,event['event_key'],event['event_type'],event['payload'],'test'))
    assert bot.send_message.await_count == sent
    assert bot.edit_message_text.await_count == sent


async def test_technical_refresh_does_not_create_missing_profit_messages(finance):
    from app.thai_telegram import ThaiTelegramDelivery
    from tests.thai.telegram_stub import telegram_mock
    root,bc,_ = await scenario(finance)
    dany = (await finance['app'].profit_queries.bindings())['daniyar_balance']['client_id']
    await finance['app'].clients.bind_chat(key='bind',actor_id=finance['actor'],client_id=dany,chat_id=-502)
    await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,reason='Cancel before delivery')
    bot = telegram_mock()
    await ThaiTelegramDelivery(finance['app'],bot).deliver_best_change(bc,refresh_only=True)
    assert bot.send_message.await_count == 0
    assert bot.edit_message_text.await_count == 0


async def test_bestchange_closed_period_and_changed_calculation_remain_atomic(finance,monkeypatch):
    from domain.accounting_policy import calculate_best_change
    root,bc,clients = await scenario(finance)
    before = await wallet_state(finance,clients)
    ops = await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')
    await finance['pool'].execute("INSERT INTO period_locks(context,period) VALUES ('ledger',$1)",DAY.replace(day=1))
    with pytest.raises(PeriodClosed):
        await finance['app'].trading.cancel(**common(finance,'closed'),deal_id=root,reason='Closed period')
    await finance['pool'].execute('DELETE FROM period_locks')
    def different(*args):
        result = calculate_best_change(*args)
        return replace(result,daniyar_usdt=result.daniyar_usdt+Decimal('0.1'),business_usdt=result.business_usdt-Decimal('0.1'))
    monkeypatch.setattr('services.exchange.history.calculate_best_change',different)
    with pytest.raises(ReversalBlocked,match='Условия BestChange'):
        await finance['app'].trading.cancel(**common(finance,'changed'),deal_id=root,reason='Policy changed')
    assert await wallet_state(finance,clients) == before
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == ops
    assert (await finance['app'].profit_queries.best_change_context(bc))['active']['active']


async def test_repeated_repricing_and_loss_keep_one_partner_allocation(finance,reference_finance):
    root,bc,clients = await scenario(finance,client_rate='36')
    _,_,reference_clients = await scenario(reference_finance,include_root=False,client_rate='36')
    before = (await finance['app'].profit_queries.best_change_context(bc))['active']['attributes']['calculation']
    assert Decimal(before['fee_usdt']) == 0
    assert Decimal(before['daniyar_usdt']) < 0
    corrected = await finance['app'].trading.correct(**common(finance,'correct'),deal_id=root,reason='Change rate',
        replacement={'mode':'wallet','receive':{'instrument':'THB_CASH','quantity':'3600'},
            'pay':{'instrument':'USDT','quantity':'100'}})
    await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,
        expected_deal_id=corrected['deal_id'],reason='Remove corrected source')
    current = (await finance['app'].profit_queries.best_change_context(bc))['active']
    assert current['attributes']['calculation'] == before
    assert await wallet_state(finance,clients) == await wallet_state(reference_finance,reference_clients)
    assert await cash_state(finance) == await cash_state(reference_finance)
    assert await total_profit(finance) == await total_profit(reference_finance)
    assert (await finance['app'].profit_queries.best_change_report(DAY.replace(day=1)))['count'] == 1
    await assert_consistent(finance)


async def test_paid_platform_fee_stays_an_explicit_atomic_blocker(finance):
    root,bc,clients = await scenario(finance)
    instant = datetime(2026,11,2,tzinfo=UTC)
    await finance['app'].profit.pay(key='pay-platform',actor_id=finance['actor'],
        business_date=instant.date(),effective_at=instant,recipient='platform',instrument='USDT',quantity='0.1')
    before = await wallet_state(finance,clients)
    cash = await cash_state(finance)
    count = await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')
    with pytest.raises(ReversalBlocked,match='отдельная ревизия'):
        await finance['app'].trading.cancel(**common(finance,'cancel'),deal_id=root,reason='Already paid')
    assert await wallet_state(finance,clients) == before
    assert await cash_state(finance) == cash
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == count
    assert (await finance['app'].profit_queries.best_change_context(bc))['active']['active']
