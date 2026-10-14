import asyncio
from datetime import UTC, date, datetime

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from domain.finance.models import IdempotencyConflict, InvalidPosting, PermissionDenied

DAY = date(2026, 10, 8)
NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


async def client(finance, *, key='client', group='partner', party_id=None):
    return await finance['app'].clients.create_client(key=key, actor_id=finance['actor'],
        name='Partner' if party_id is None else None, party_id=party_id, group_code=group)


async def cash(finance, client_id, *, key='deposit', deposit=True, quantity='100000', instrument='THB_CASH', rate='35'):
    service = finance['app'].wallets.deposit if deposit else finance['app'].wallets.withdraw
    return await service(key=key, actor_id=finance['actor'], client_id=client_id, instrument=instrument,
        quantity=quantity, quoted_rate=rate, business_date=DAY, effective_at=NOW)


async def test_partner_deposit_and_partial_return_use_one_wallet_balance(finance):
    created = await client(finance)
    first = await cash(finance, created['client_id'])
    assert await cash(finance, created['client_id']) == first
    await cash(finance, created['client_id'], key='return', deposit=False, quantity='30000', rate=None)
    wallets = await finance['app'].client_queries.wallets(created['client_id'])
    wallet = next(row for row in wallets if row['instrument_code']=='THB_CASH')
    loan = (await finance['app'].loans.balances())['items'][0]
    assert loan['balance'] == wallet['balance'] == 70000
    assert loan['carrying_usdt'] == wallet['carrying_usdt'] == 2000
    assert loan['ledger_account_id'] == wallet['ledger_account_id']
    assert loan['debt_direction'] == 'exchange_owes_client'
    assert len(wallets) == 8
    assert await finance['pool'].fetchval('SELECT count(*) FROM capital_contributions') == 0
    assert await finance['pool'].fetchval("SELECT coalesce(sum(b.balance),0) FROM ledger_accounts a JOIN posted_account_balances b ON b.account_id=a.id WHERE a.account_class='income'") == 0
    history = await finance['app'].loans.history(created['client_id'], 'THB_CASH')
    assert [row['quantity_delta'] for row in history['items']] == [-30000, 100000]
    assert history['items'][0]['balance_after'] == 70000
    # Loan reads ignore deliberately damaged projection caches.
    await finance['pool'].execute('UPDATE account_balances SET debit_signed_native=0,debit_signed_usdt=0 WHERE account_id=$1', wallet['ledger_account_id'])
    assert (await finance['app'].loans.balances())['items'][0]['balance'] == 70000


async def test_group_change_has_only_crm_audit_and_wallet_history_stays_exact(finance):
    created = await client(finance)
    cid = created['client_id']
    await cash(finance, cid)
    before = await finance['app'].client_queries.wallet_history(cid, 'THB_CASH')
    financial_count = await finance['pool'].fetchval('SELECT count(*) FROM financial_operations')
    outbox_count = await finance['pool'].fetchval('SELECT count(*) FROM telegram_outbox')
    kwargs = {'key':'group', 'actor_id':finance['actor'], 'client_id':cid, 'group_code':'balance', 'reason':'Reclassify'}
    result = await finance['app'].clients.assign_group(**kwargs)
    assert await finance['app'].clients.assign_group(**kwargs) == result
    assert (await finance['app'].loans.balances())['items'] == []
    assert await finance['app'].client_queries.wallet_history(cid, 'THB_CASH') == before
    assert await finance['pool'].fetchval('SELECT count(*) FROM financial_operations') == financial_count
    assert await finance['pool'].fetchval('SELECT count(*) FROM telegram_outbox') == outbox_count
    assert await finance['pool'].fetchval("SELECT count(*) FROM client_directory_events WHERE kind='client_group'") == 1
    with pytest.raises(IdempotencyConflict):
        await finance['app'].clients.assign_group(**{**kwargs, 'group_code':'partner'})
    await finance['app'].clients.assign_group(**{**kwargs, 'key':'back', 'group_code':'partner'})
    assert (await finance['app'].loans.balances())['items'][0]['balance'] == 100000


async def test_duplicate_group_names_and_owner_role_never_classify_as_loan(finance):
    await finance['app'].clients.create_group(key='same-name', actor_id=finance['actor'], code='other', name='Партнер')
    await finance['app'].clients.rename_group(key='rename', actor_id=finance['actor'], code='partner', name='VIP')
    included = await client(finance, key='included')
    excluded = await client(finance, key='excluded', group='other')
    owner = await finance['pool'].fetchval("SELECT party_id FROM business_participants WHERE code='pe'")
    owner_client = await client(finance, key='owner-client', group='balance', party_id=owner)
    for created in (included, excluded, owner_client):
        await cash(finance, created['client_id'], key=f"deposit-{created['client_id']}")
    assert {r['client_id'] for r in (await finance['app'].loans.balances())['items']} == {included['client_id']}
    assert await finance['pool'].fetchval("SELECT count(*) FROM party_roles WHERE party_id=$1 AND role='owner'", owner) == 1
    with pytest.raises(asyncpg.CheckViolationError):
        await finance['pool'].execute("UPDATE client_groups SET code='new_partner' WHERE code='partner'")


async def test_negative_zero_wallets_and_crossing_zero_preserve_signs(finance):
    provider = await client(finance, key='provider', group='balance')
    borrower = await client(finance, key='borrower')
    cid = borrower['client_id']
    await cash(finance, provider['client_id'], quantity='10000')
    await cash(finance, cid, key='negative', deposit=False, quantity='3500')
    row = (await finance['app'].loans.balances())['items'][0]
    assert row['balance'] == -3500
    assert row['carrying_usdt'] == -100
    assert row['debt_direction'] == 'client_owes_exchange'
    # Settle a negative wallet at its historical valuation, leaving no cost tail.
    await cash(finance, cid, key='settle', quantity='3500', rate='36')
    assert (await finance['app'].loans.balances())['items'] == []
    audit = (await finance['app'].loans.balances(include_zero=True))['items']
    assert len(audit) == 8 and all(r['balance']==0 and r['carrying_usdt']==0 and r['debt_direction']=='settled' for r in audit)


async def test_instrument_and_currency_totals_and_client_pagination(finance):
    first = await client(finance, key='first')
    second = await client(finance, key='second')
    await cash(finance, first['client_id'], key='cash', quantity='1000')
    await cash(finance, first['client_id'], key='bank', quantity='2000', instrument='THB_BANK')
    await cash(finance, second['client_id'], key='second-cash', quantity='4000')
    totals = await finance['app'].loans.totals()
    instruments = {r['instrument_code']:r for r in totals['by_instrument']}
    assert instruments['THB_CASH']['balance'] == 5000
    assert instruments['THB_BANK']['balance'] == 2000
    assert next(r for r in totals['by_currency'] if r['currency_code']=='THB')['balance'] == 7000
    page = await finance['app'].loans.balances(limit=1)
    assert len(page['items']) == 2  # All nonzero instruments of one client stay on the same page.
    second_page = await finance['app'].loans.balances(limit=1, after_client_id=page['next_after_client_id'])
    assert {r['client_id'] for r in second_page['items']} == {second['client_id']}


async def test_parallel_registration_and_deposit_have_no_second_loan_record(finance):
    created = await asyncio.gather(*(client(finance) for _ in range(4)))
    assert len({r['client_id'] for r in created}) == 1
    results = await asyncio.gather(*(cash(finance, created[0]['client_id']) for _ in range(4)))
    assert len({r.id for r in results}) == 1
    assert (await finance['app'].loans.balances())['items'][0]['balance'] == 100000
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients') == 1
    assert await finance['pool'].fetchval('SELECT count(*) FROM client_wallet_accounts') == 8


async def test_loans_api_strings_history_and_group_change(finance):
    cid = (await client(finance))['client_id']
    secret = 'x'*32
    app = create_thai_finance_api(container=finance['app'], jwt_secret=secret)
    token = create_access_token(tg_user_id=12345, secret=secret, ttl_seconds=60)
    headers = {'Authorization': f'Bearer {token}'}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
        assert (await http.get('/api/v1/loans')).status_code == 401
        payload = {'idempotency_key':'api-deposit', 'quantity':'100000', 'quoted_rate':'35', 'effective_at':NOW.isoformat()}
        deposited = await http.post(f'/api/v1/clients/{cid}/wallets/THB_CASH/deposits', json=payload, headers=headers)
        assert deposited.status_code == 200
        items = (await http.get('/api/v1/loans', headers=headers)).json()['items']
        assert items[0]['balance'] == '100000.00000000'
        assert (await http.get(items[0]['history_url'], headers=headers)).json()['items'][0]['balance_after'] == '100000.00000000'
        assert (await http.get('/api/v1/loans/totals', headers=headers)).status_code == 200
        changed = await http.put(f'/api/v1/clients/{cid}/group', json={'idempotency_key':'api-group', 'group_code':'balance', 'reason':'Change'}, headers=headers)
        assert changed.status_code == 200
        assert (await http.get('/api/v1/loans', headers=headers)).json()['items'] == []
        assert (await http.get(f'/api/v1/loans/{cid}/wallets/THB_CASH/history', headers=headers)).status_code == 422
        assert (await http.get('/api/v1/loans?limit=0', headers=headers)).status_code == 422


async def test_directory_permissions_and_failure_leave_no_client_or_audit(finance, monkeypatch):
    from db_asyncpg.thai_clients.repository import ClientsRepository
    original = ClientsRepository.save_event
    async def fail_after_audit(self, *args):
        await original(self, *args)
        raise RuntimeError('Failed after directory audit')
    monkeypatch.setattr(ClientsRepository, 'save_event', fail_after_audit)
    with pytest.raises(RuntimeError):
        await client(finance)
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients') == 0
    assert await finance['pool'].fetchval('SELECT count(*) FROM client_directory_events') == 0
    assert await finance['pool'].fetchval('SELECT count(*) FROM client_wallet_accounts') == 0
    await finance['pool'].execute('DELETE FROM user_roles WHERE user_id=$1', finance['actor'])
    with pytest.raises(PermissionDenied):
        await client(finance)


async def test_missing_group_and_changed_deposit_key_do_not_change_money(finance):
    with pytest.raises(InvalidPosting):
        await client(finance, group='Партнер')
    cid = (await client(finance))['client_id']
    await cash(finance, cid)
    with pytest.raises(IdempotencyConflict):
        await cash(finance, cid, quantity='100001')
    assert (await finance['app'].loans.balances())['items'][0]['balance'] == 100000


async def test_group_api_cors_and_permission_enforcement(finance):
    secret = 'x'*32
    app = create_thai_finance_api(container=finance['app'], jwt_secret=secret, cors_origins=['http://localhost:3000'])
    token = create_access_token(tg_user_id=12345, secret=secret, ttl_seconds=60)
    headers = {'Authorization': f'Bearer {token}'}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
        preflight = await http.options('/api/v1/clients/1/group', headers={'Origin':'http://localhost:3000',
            'Access-Control-Request-Method':'PUT', 'Access-Control-Request-Headers':'Authorization,Content-Type'})
        assert preflight.status_code == 200
        groups = (await http.get('/api/v1/client-groups', headers=headers)).json()
        assert next(row for row in groups if row['code']=='partner')['exposes_in_loans'] is True
        added = await http.post('/api/v1/client-groups', json={'idempotency_key':'api-group-create', 'code':'new', 'name':'Партнер'}, headers=headers)
        assert added.status_code == 200 and added.json()['exposes_in_loans'] is False
        created = await http.post('/api/v1/clients', json={'idempotency_key':'api-client', 'name':'New client', 'group_code':'new'}, headers=headers)
        assert created.status_code == 200
        assert len((await http.get('/api/v1/clients', headers=headers)).json()['items']) == 1
        renamed = await http.put('/api/v1/client-groups/new/name', json={'idempotency_key':'api-rename', 'name':'Other'}, headers=headers)
        assert renamed.status_code == 200
        await finance['pool'].execute('DELETE FROM user_roles WHERE user_id=$1', finance['actor'])
        await finance['pool'].execute("INSERT INTO user_roles VALUES ($1,'cashier')", finance['actor'])
        denied = await http.post('/api/v1/clients', json={'idempotency_key':'denied', 'name':'Forbidden'}, headers=headers)
        assert denied.status_code == 403 and denied.json()['error']['code']=='permission_denied'
