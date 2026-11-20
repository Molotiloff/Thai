"""Expense cancellation must equal the history posted without that expense."""
from datetime import timedelta
from decimal import Decimal

import pytest

from domain.finance.models import IdempotencyConflict, PeriodClosed, ReversalBlocked
from tests.thai.test_bestchange_history import cash_state
from tests.thai.test_exchange_history import (
    assert_consistent,
    client,
    common,
    total_profit,
    wallet_state,
)
from tests.thai.test_expenses import bind_wallets, record, totals
from tests.thai.test_posting import DAY, INSTANT, fund
from tests.thai.test_trading import trade


async def scenario(db, *, include_root=True, payer='business'):
    wallets = await bind_wallets(db)
    buyer = await client(db, 'buyer')
    await fund(db, quantity=Decimal('35000'))
    await fund(db, key='usdt-fund', instrument='USDT', quantity=Decimal('1000'))
    root = await record(db, key='old-expense', payer=payer, amount='3500') if include_root else None
    # The replenishment has an agreed rate; removing an older withdrawal changes WAC.
    deal = await trade(db, key='cash-purchase', client_id=buyer, quantity='3400', rate='34')
    await db['app'].trading.post(**common(db, 'cash-purchase-post'), deal_id=deal)
    later = await record(db, key='later-expense', payer='business', amount='700', equal_split=True)
    await db['app'].wallets.withdraw(**common(db, 'repayment'), client_id=wallets['pe'],
        instrument='USDT', quantity='5')
    return root, later, [*wallets.values(), buyer]


@pytest.mark.parametrize('payer', ['business', 'pe', 'daniyar'])
async def test_old_expense_cancel_recalculates_profit_and_preserves_later_terms(finance, reference_finance, payer):
    root, later, clients = await scenario(finance, payer=payer)
    _, _, reference_clients = await scenario(reference_finance, include_root=False, payer=payer)
    before = await finance['app'].expense_queries.detail(await finance['pool'].fetchval(
        'SELECT id FROM expenses WHERE operation_id=$1', later.id))
    result = await finance['app'].expenses.reverse(**common(finance, 'undo'), operation_id=root.id, reason='Error')
    assert result.reversal_of == root.id
    assert result.business_date == DAY
    assert await cash_state(finance) == await cash_state(reference_finance)
    assert await wallet_state(finance, clients) == await wallet_state(reference_finance, reference_clients)
    assert await total_profit(finance) == await total_profit(reference_finance)
    assert await totals(finance) == await totals(reference_finance)
    current = await finance['pool'].fetchrow('SELECT e.* FROM expenses e JOIN active_financial_originals o ON o.id=e.operation_id')
    after = await finance['app'].expense_queries.detail(current['id'])
    for field in ('amount', 'amount_usdt', 'comment', 'quoted_rate', 'business_date'):
        assert after[field] == before[field]
    assert [{k:v for k,v in a.items() if k != 'expense_id'} for a in after['allocations']] == [
        {k:v for k,v in a.items() if k != 'expense_id'} for a in before['allocations']]
    assert await finance['app'].expenses.reverse(**common(finance, 'undo'), operation_id=root.id, reason='Error') == result
    with pytest.raises(IdempotencyConflict):
        await finance['app'].expenses.reverse(**common(finance, 'undo'), operation_id=root.id, reason='Different')
    await assert_consistent(finance)


async def test_expense_cancel_uses_original_date_and_closed_tail_rolls_back(finance):
    root, _, _ = await scenario(finance)
    before = await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')
    await finance['pool'].execute("INSERT INTO period_locks(context,period,closed_by_operation_id) VALUES ('expenses',$1,$2)", DAY.replace(day=1), root.id)
    with pytest.raises(PeriodClosed):
        await finance['app'].expenses.reverse(**common(finance, 'blocked'), operation_id=root.id, reason='Error')
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == before
    await finance['pool'].execute("DELETE FROM period_locks WHERE context='expenses'")
    args = common(finance, 'undo')
    args.update(business_date=DAY+timedelta(days=3))
    result = await finance['app'].expenses.reverse(**args, operation_id=root.id, reason='Error')
    assert result.business_date == DAY


async def test_expense_cancel_blocks_unsupported_document_atomically(finance):
    root, _, _ = await scenario(finance)
    # An unsupported business document/dependency must not be silently dropped.
    await fund(finance, key="unsupported-document")
    before = await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')
    with pytest.raises(ReversalBlocked, match='ревизия'):
        await finance['app'].expenses.reverse(**common(finance, 'blocked'), operation_id=root.id, reason='Error')
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == before


async def test_expense_technical_replay_edits_existing_telegram_card_without_sending(finance):
    from app.thai_telegram import ThaiTelegramDelivery
    from services.finance.outbox import OutboxEvent
    from tests.thai.telegram_stub import telegram_mock

    root, later, _ = await scenario(finance)
    bot = telegram_mock()
    delivery = ThaiTelegramDelivery(finance['app'], bot, -999)
    await delivery.deliver_expense(later.id)
    sent = bot.send_message.await_count
    await finance['app'].expenses.reverse(**common(finance, 'undo'), operation_id=root.id, reason='Error')
    row = await finance['pool'].fetchrow("SELECT * FROM telegram_outbox WHERE event_type='expense.revalued'")
    await delivery.deliver(OutboxEvent(1,row['event_key'],row['event_type'],row['payload'],'test'))
    assert bot.send_message.await_count == sent
    assert bot.edit_message_text.await_count == sent
    assert 'Отменён расход' not in bot.edit_message_text.call_args.args[0]
    await finance['pool'].execute("DELETE FROM telegram_message_links WHERE purpose='expense'")
    await delivery.deliver(OutboxEvent(1,row['event_key'],row['event_type'],row['payload'],'test'))
    assert bot.send_message.await_count == sent


async def test_expense_cancel_preserves_bestchange_terms_and_partner_share(finance, reference_finance):
    from tests.thai.test_profit import configure

    for db in (finance, reference_finance):
        await configure(db)
    root, _, clients = await scenario(finance)
    _, _, reference_clients = await scenario(reference_finance, include_root=False)
    ids = []
    for db in (finance, reference_finance):
        operation = await db['app'].best_change.record(**common(db, 'best'), operation='purchase',
            quantity='100', market_rate='35', client_rate='34', mode='cash')
        ids.append(await db['pool'].fetchval('SELECT id FROM deals WHERE operation_id=$1', operation.id))
    before = (await finance['app'].profit_queries.best_change_context(ids[0]))['active']['attributes']['calculation']
    await finance['app'].expenses.reverse(**common(finance, 'undo'), operation_id=root.id, reason='Error')
    after = (await finance['app'].profit_queries.best_change_context(ids[0]))['active']['attributes']['calculation']
    assert before == after
    assert await cash_state(finance) == await cash_state(reference_finance)
    assert await wallet_state(finance, clients) == await wallet_state(reference_finance, reference_clients)
    assert await total_profit(finance) == await total_profit(reference_finance)
    await assert_consistent(finance)


async def test_parallel_cancel_and_api_retry_do_not_duplicate_history(finance):
    import asyncio

    from httpx import ASGITransport, AsyncClient

    from api.auth import create_access_token
    from api.thai_finance import create_thai_finance_api

    root, _, _ = await scenario(finance)
    results = await asyncio.gather(*(finance['app'].expenses.reverse(**common(finance, 'undo'),
        operation_id=root.id, reason='Error') for _ in range(3)))
    assert results[0] == results[1] == results[2]
    count = await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')
    api = create_thai_finance_api(container=finance['app'], jwt_secret=finance['config'].jwt_secret)
    token = create_access_token(tg_user_id=12345, secret=finance['config'].jwt_secret, ttl_seconds=3600)
    async with AsyncClient(transport=ASGITransport(app=api), base_url='http://test',
        headers={'Authorization': 'Bearer '+token}) as http:
        response = await http.post(f'/api/v1/expense-operations/{root.id}/reversal',
            json={'idempotency_key':'undo', 'reason':'Error', 'effective_at':INSTANT.isoformat()})
    assert response.status_code == 200, response.text
    assert response.json()['id'] == results[0].id
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == count
