"""Historical correction must equal posting the corrected history from scratch."""

import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from domain.finance.models import (
    IdempotencyConflict,
    InvalidPosting,
    PeriodClosed,
    ReversalBlocked,
    canonical_json,
)
from tests.thai.test_posting import DAY, INSTANT


def common(finance, key):
    return {'key': key, 'actor_id': finance['actor'], 'business_date': DAY, 'effective_at': INSTANT}


async def client(finance, key):
    return (await finance['app'].clients.create_client(
        key=key, actor_id=finance['actor'], name=key))['client_id']


async def exchange(finance, key, cid, receive, pay):
    app = finance['app']
    return await app.trading.create_and_post(**common(finance, key), client_id=cid,
        receive={'instrument': receive[0], 'quantity': receive[1]},
        pay={'instrument': pay[0], 'quantity': pay[1]}, presentation={})


async def correction(finance, deal_id, quantity, key='correct'):
    return await finance['app'].trading.correct(**common(finance, key), deal_id=deal_id,
        reason='Исправить историческую сумму', replacement={'mode': 'wallet',
            'receive': {'instrument': 'THB_CASH', 'quantity': quantity},
            'pay': {'instrument': 'USDT', 'quantity': '100'}})


async def wallet_state(finance, clients):
    result = []
    for cid in clients:
        rows = await finance['pool'].fetch("""
            SELECT w.instrument_code,b.balance,b.carrying_usdt,b.unvalued_native
            FROM client_wallet_accounts w JOIN posted_account_balances b ON b.account_id=w.ledger_account_id
            WHERE w.client_id=$1 ORDER BY w.instrument_code
        """, cid)
        result.append([tuple(row) for row in rows])
    return result


async def total_profit(finance):
    return await finance['pool'].fetchval("""
        SELECT coalesce(sum(CASE l.side WHEN 'credit' THEN l.amount_usdt ELSE -l.amount_usdt END),0)
        FROM journal_lines l JOIN financial_operations o ON o.id=l.operation_id
        JOIN ledger_accounts a ON a.id=l.account_id
        WHERE o.status='posted' AND a.account_class IN ('income','expense')
    """)


async def scenario(finance, prefix, quantity):
    first, second = await client(finance, prefix+'a'), await client(finance, prefix+'b')
    original = await exchange(finance, prefix+'root', first, ('THB_CASH', quantity), ('USDT', '100'))
    await exchange(finance, prefix+'next', first, ('USDT', '50'), ('THB_CASH', '1600'))
    await finance['app'].wallet_transfers.transfer(**common(finance, prefix+'transfer'),
        source_client_id=first, target_client_id=second, instrument='THB_CASH', quantity='700')
    await exchange(finance, prefix+'recipient-exchange', second, ('THB_CASH', '300'), ('USDT', '10'))
    await finance['app'].conversions.transfer(**common(finance, prefix+'convert'), client_id=second,
        source_instrument='THB_CASH', target_instrument='THB_BANK', quantity='100', fee='5',
        quoted_rate=None)
    return original, [first, second]


async def assert_consistent(finance):
    # Projection rebuild cannot change the result: both profit and principal must
    # come from the immutable journal, not an out-of-band balance rewrite.
    query = 'SELECT account_id,debit_signed_native,debit_signed_usdt,debit_signed_unvalued FROM account_balances ORDER BY account_id'
    before = await finance['pool'].fetch(query)
    await finance['app'].projections.rebuild(finance['actor'])
    assert await finance['pool'].fetch(query) == before
    assert not await finance['pool'].fetchval("""
        SELECT EXISTS(SELECT 1 FROM account_balances b JOIN posted_account_balances p ON p.account_id=b.account_id
            JOIN ledger_accounts a ON a.id=b.account_id
            WHERE b.debit_signed_native<>p.balance*CASE a.normal_side WHEN 'debit' THEN 1 ELSE -1 END
                OR b.debit_signed_usdt<>p.carrying_usdt*CASE a.normal_side WHEN 'debit' THEN 1 ELSE -1 END)
    """)


@pytest.mark.parametrize('corrected_quantity', ['3300', '1000', '5000'])
async def test_historical_chain_profit_equals_clean_history(finance, reference_finance, corrected_quantity):
    _, reference_clients = await scenario(reference_finance, 'reference-', corrected_quantity)
    expected_wallets = await wallet_state(reference_finance, reference_clients)
    expected_profit = await total_profit(reference_finance)
    original, clients = await scenario(finance, 'corrected-', '3500')
    result = await correction(finance, original, corrected_quantity)
    assert len(result['recalculated_operations']) == 4
    assert await wallet_state(finance, clients) == expected_wallets
    assert await total_profit(finance) == expected_profit
    assert await correction(finance, original, corrected_quantity) == result
    assert await total_profit(finance) == expected_profit
    before = await wallet_state(finance, clients)
    await assert_consistent(finance)
    assert await wallet_state(finance, clients) == before


async def test_historical_trade_profit_changes_and_replay_is_silent(finance):
    cid = await client(finance, 'client')
    original = await exchange(finance, 'root', cid, ('THB_CASH', '3500'), ('USDT', '100'))
    later = await exchange(finance, 'later', cid, ('USDT', '50'), ('THB_CASH', '1600'))
    assert await total_profit(finance) == Decimal('4.28571429')
    outbox_before = await finance['pool'].fetchval('SELECT count(*) FROM telegram_outbox')
    result = await correction(finance, original, '3300')
    assert await total_profit(finance) == Decimal('1.51515152')
    later_context = await finance['app'].trading_queries.exchange_context(later)
    assert Decimal(later_context['active']['attributes']['result_usdt']) == Decimal('1.51515152')
    assert later_context['root']['id'] == later
    # Root correction plus a card refresh; no financial notification for replay.
    assert await finance['pool'].fetchval('SELECT count(*) FROM telegram_outbox') == outbox_before + 3
    assert await finance['pool'].fetchval("SELECT count(*) FROM telegram_outbox WHERE event_type='exchange.revalued'") == 1
    # Correcting the root again must use its original place in history.
    await correction(finance, result['deal_id'], '3200', key='correct-again')
    assert await total_profit(finance) == 0


@pytest.mark.parametrize('quantity', ['1000', '3300', '5000'])
async def test_cash_settlements_reprice_wallet_and_profit_like_clean_history(finance, reference_finance, quantity):
    async def run(database, prefix, original_quantity):
        cid = await client(database, prefix)
        root = await exchange(database, prefix+'root', cid, ('THB_CASH', original_quantity), ('USDT', '100'))
        await database['app'].wallets.deposit(**common(database, prefix+'deposit'), client_id=cid,
            instrument='THB_CASH', quantity='2000', quoted_rate='35')
        await database['app'].wallets.withdraw(**common(database, prefix+'withdraw'), client_id=cid,
            instrument='THB_CASH', quantity='1000')
        return root, cid
    _, reference = await run(reference_finance, 'reference', quantity)
    root, cid = await run(finance, 'actual', '3500')
    result = await correction(finance, root, quantity)
    assert len(result['recalculated_operations']) == 2
    assert await wallet_state(finance, [cid]) == await wallet_state(reference_finance, [reference])
    assert await total_profit(finance) == await total_profit(reference_finance)
    actual_cash = await finance['pool'].fetch('SELECT instrument_code,quantity,cost_usdt FROM inventory_balances ORDER BY instrument_code')
    reference_cash = await reference_finance['pool'].fetch('SELECT instrument_code,quantity,cost_usdt FROM inventory_balances ORDER BY instrument_code')
    assert actual_cash == reference_cash
    await assert_consistent(finance)


async def test_cross_exchange_internal_valuation_propagates_to_other_currency_and_client(finance, reference_finance):
    async def run(database, prefix, quantity):
        first, second = await client(database, prefix+'a'), await client(database, prefix+'b')
        root = await exchange(database, prefix+'root', first, ('THB_CASH', quantity), ('USDT', '100'))
        await exchange(database, prefix+'cross', first, ('THB_CASH', '1000'), ('EUR', '30'))
        await database['app'].wallet_transfers.transfer(**common(database, prefix+'transfer'),
            source_client_id=first, target_client_id=second, instrument='EUR', quantity='10')
        await exchange(database, prefix+'sale', second, ('EUR', '5'), ('USDT', '6'))
        return root, [first, second]
    _, reference_clients = await run(reference_finance, 'reference', '3300')
    root, clients = await run(finance, 'actual', '3500')
    result = await correction(finance, root, '3300')
    assert len(result['recalculated_operations']) == 3
    assert await wallet_state(finance, clients) == await wallet_state(reference_finance, reference_clients)
    assert await total_profit(finance) == await total_profit(reference_finance)


async def test_unknown_deposit_is_revalued_at_corrected_exchange_rate(finance, reference_finance):
    async def run(database, prefix, amount):
        cid = await client(database, prefix)
        await database['app'].wallets.deposit(**common(database, prefix+'deposit'), client_id=cid,
            instrument='THB_CASH', quantity='1000')
        root = await exchange(database, prefix+'root', cid, ('USDT', '100'), ('THB_CASH', amount))
        await database['app'].wallets.withdraw(**common(database, prefix+'withdraw'), client_id=cid,
            instrument='THB_CASH', quantity='500')
        return cid, root
    reference, _ = await run(reference_finance, 'reference', '3500')
    cid, root = await run(finance, 'actual', '3300')
    await finance['app'].trading.correct(**common(finance, 'correct'), deal_id=root, reason='Изменить курс',
        replacement={'mode': 'wallet', 'receive': {'instrument': 'USDT', 'quantity': '100'},
            'pay': {'instrument': 'THB_CASH', 'quantity': '3500'}})
    assert await wallet_state(finance, [cid]) == await wallet_state(reference_finance, [reference])
    assert await total_profit(finance) == await total_profit(reference_finance)
    actual_cash = await finance['pool'].fetch('SELECT instrument_code,quantity,cost_usdt,unvalued_quantity FROM inventory_balances ORDER BY instrument_code')
    reference_cash = await reference_finance['pool'].fetch('SELECT instrument_code,quantity,cost_usdt,unvalued_quantity FROM inventory_balances ORDER BY instrument_code')
    assert actual_cash == reference_cash
    await assert_consistent(finance)


async def test_cash_adjustments_and_transfers_replay_with_original_message_links(finance, reference_finance):
    async def run(database, prefix, amount):
        cid = await client(database, prefix)
        root = await exchange(database, prefix+'root', cid, ('THB_CASH', amount), ('USDT', '100'))
        incoming = await database['app'].manual_cash.adjust(**common(database, prefix+'incoming'),
            instrument='THB_CASH', quantity='2000', chat_id=-999)
        await database['app'].cash_transfers.transfer(**common(database, prefix+'transfer'),
            source_id=database['cash']['THB_CASH'], target_id=database['cash']['THB_BANK'],
            quantity=Decimal('1000'))
        outgoing = await database['app'].manual_cash.adjust(**common(database, prefix+'outgoing'),
            instrument='THB_BANK', quantity='-300', chat_id=-999)
        return root, incoming.id, outgoing.id
    await run(reference_finance, 'reference', '3300')
    root, _, outgoing_id = await run(finance, 'actual', '3500')
    await finance['pool'].execute("""
        INSERT INTO telegram_message_links(operation_id,chat_id,message_id,purpose)
        VALUES ($1,-999,888,'cash_adjustment')
    """, outgoing_id)
    result = await correction(finance, root, '3300')
    assert len(result['recalculated_operations']) == 3
    actual_cash = await finance['pool'].fetch('SELECT instrument_code,quantity,cost_usdt FROM inventory_balances ORDER BY instrument_code')
    reference_cash = await reference_finance['pool'].fetch('SELECT instrument_code,quantity,cost_usdt FROM inventory_balances ORDER BY instrument_code')
    assert actual_cash == reference_cash
    current_outgoing_id = result['recalculated_operations'][-1]['operation_id']
    assert await finance['app'].queries.cash_operation_by_message(-999, 888) == current_outgoing_id
    await finance['app'].manual_cash.undo_by_message(**{k:v for k,v in common(finance, 'undo').items() if k!='key'},
        chat_id=-999, message_id=888)
    assert await finance['pool'].fetchval("SELECT quantity FROM inventory_balances WHERE instrument_code='THB_BANK'") == 1000
    await assert_consistent(finance)


async def test_correction_keeps_original_accounting_date_and_quote_time(finance):
    cid = await client(finance, 'client')
    root = await exchange(finance, 'root', cid, ('THB_CASH', '3500'), ('USDT', '100'))
    await exchange(finance, 'later', cid, ('USDT', '50'), ('THB_CASH', '1600'))
    now = INSTANT + timedelta(days=40)
    result = await finance['app'].trading.correct(key='today', actor_id=finance['actor'],
        business_date=now.date(), effective_at=now, deal_id=root, reason='Исправление сегодня',
        replacement={'mode': 'wallet', 'receive': {'instrument': 'THB_CASH', 'quantity': '3300'},
            'pay': {'instrument': 'USDT', 'quantity': '100'}})
    rows = await finance['pool'].fetch('SELECT business_date,effective_at FROM financial_operations WHERE id=ANY($1::bigint[])',
        [result['operation_id'], result['reversal_operation_id']])
    assert all(row['business_date'] == DAY and row['effective_at'] == INSTANT for row in rows)
    assert await total_profit(finance) == Decimal('1.51515152')


async def test_explicit_dependency_is_transferred_to_replayed_operations(finance):
    cid = await client(finance, 'client')
    root = await exchange(finance, 'root', cid, ('THB_CASH', '3500'), ('USDT', '100'))
    root_op = (await finance['app'].trading_queries.detail(root))['operation_id']
    later = await finance['app'].trading.create(key='later', actor_id=finance['actor'], client_id=cid,
        mode='wallet', receive={'instrument': 'USDT', 'quantity': '50'},
        pay={'instrument': 'THB_CASH', 'quantity': '1600'})
    await finance['app'].trading.ready(key='ready', actor_id=finance['actor'], deal_id=later['deal_id'])
    await finance['app'].trading.post(**common(finance, 'post'), deal_id=later['deal_id'],
        dependency_operation_ids=(root_op,))
    result = await correction(finance, root, '3300')
    assert await finance['pool'].fetchval('SELECT EXISTS(SELECT 1 FROM financial_operation_dependencies WHERE source_operation_id=$1 AND dependent_operation_id=$2)',
        result['operation_id'], result['recalculated_operations'][0]['operation_id'])


async def test_failed_historical_replacement_rolls_back_entire_tail(finance):
    cid = await client(finance, 'client')
    original = await exchange(finance, 'root', cid, ('THB_CASH', '3500'), ('USDT', '100'))
    await exchange(finance, 'later', cid, ('USDT', '50'), ('THB_CASH', '1600'))
    before = await wallet_state(finance, [cid])
    count = await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')
    with pytest.raises(InvalidPosting):
        await finance['app'].trading.correct(**common(finance, 'bad'), deal_id=original,
            reason='Ошибка', replacement={'mode': 'wallet',
                'receive': {'instrument': 'USD_BL', 'quantity': '10'},
                'pay': {'instrument': 'EUR', 'quantity': '9'}})
    assert await wallet_state(finance, [cid]) == before
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == count
    assert await finance['pool'].fetchval('SELECT count(*) FROM deal_reversals') == 0


async def test_closed_tail_period_remains_protected(finance):
    cid = await client(finance, 'client')
    original = await exchange(finance, 'root', cid, ('THB_CASH', '3500'), ('USDT', '100'))
    await exchange(finance, 'later', cid, ('USDT', '50'), ('THB_CASH', '1600'))
    await finance['pool'].execute("INSERT INTO period_locks(context,period) VALUES ('ledger',$1)",
        date(DAY.year, DAY.month, 1))
    before = await wallet_state(finance, [cid])
    with pytest.raises(PeriodClosed):
        await correction(finance, original, '3300')
    assert await wallet_state(finance, [cid]) == before
    assert await finance['pool'].fetchval('SELECT count(*) FROM deal_reversals') == 0


async def test_quote_provenance_metadata_keeps_legacy_retries_compatible(finance, monkeypatch):
    cid = await client(finance, 'client')
    service = finance['app'].wallets
    posting = service._posting

    class LegacyPosting:
        async def post(self, command, builder, **options):
            details = json.loads(command.details_json)
            details.pop('rate_source')
            return await posting.post(replace(command, details_json=canonical_json(details)), builder, **options)

    options = {**common(finance, 'legacy'), 'client_id': cid, 'instrument': 'USDT', 'quantity': '10'}
    with monkeypatch.context() as context:
        context.setattr(service, '_posting', LegacyPosting())
        original = await service.deposit(**options)
    assert await service.deposit(**options) == original
    with pytest.raises(IdempotencyConflict):
        await service.deposit(**{**options, 'quantity': '11'})


async def test_unreplayable_document_is_explicit_atomic_blocker(finance):
    cid = await client(finance, 'client')
    original = await exchange(finance, 'root', cid, ('THB_CASH', '3500'), ('USDT', '100'))
    # Closing/zeroing a client owns additional business state; it must not be
    # silently replayed as an ordinary wallet adjustment.
    request = await finance['app'].wallet_adjustments.prepare_zero(key='zero', actor_id=finance['actor'], client_id=cid)
    assert request['client_id'] == cid
    await finance['app'].wallet_adjustments.resolve_zero(**common(finance, 'zero'), confirmed=True)
    before = await wallet_state(finance, [cid])
    with pytest.raises(ReversalBlocked, match='отдельная ревизия'):
        await correction(finance, original, '3300')
    assert await wallet_state(finance, [cid]) == before
