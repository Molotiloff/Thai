import asyncio
from decimal import Decimal

from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from db_asyncpg.thai_reports import ReportQueries
from tests.thai.test_expenses import common
from tests.thai.test_posting import DAY

PERIOD = DAY.replace(day=1)


async def test_dashboard_separates_cash_wallets_capital_and_partner_subset(finance):
    app = finance['app']
    client = (await app.clients.create_client(key='client',actor_id=finance['actor'],name='Partner',group_code='partner'))['client_id']
    await app.wallets.deposit(**common(finance,'deposit'),client_id=client,instrument='THB_BANK',quantity='3500',quoted_rate='35')
    report = await ReportQueries(finance['pool']).dashboard(PERIOD)
    cash = next(r for r in report['cash'] if r['instrument_code']=='THB_BANK')
    wallet = next(r for r in report['wallets'] if r['instrument_code']=='THB_BANK')
    assert len(report['cash'])==8
    assert cash['balance']==3500 and cash['carrying_usdt']==100
    assert wallet['exchange_owes']==wallet['partner_loans']==3500
    assert report['capital_usdt']==report['profit']['net_profit_usdt']==0
    assert report['snapshot'] and report['as_of'].tzinfo


async def test_dashboard_read_snapshot_does_not_mix_parallel_wallet_postings(finance):
    app=finance['app']
    client=(await app.clients.create_client(key='client',actor_id=finance['actor'],name='Customer'))['client_id']
    async def writer():
        for i in range(8):
            await app.wallets.deposit(**common(finance,f'deposit:{i}'),client_id=client,instrument='THB_CASH',quantity='35',quoted_rate='35')
    async def reader():
        for _ in range(12):
            report=await ReportQueries(finance['pool']).dashboard(PERIOD)
            cash=next(r for r in report['cash'] if r['instrument_code']=='THB_CASH')
            wallet=next(r for r in report['wallets'] if r['instrument_code']=='THB_CASH')
            assert cash['balance']==wallet['exchange_owes']
            assert cash['carrying_usdt']==wallet['carrying_usdt']
            assert report['profit']['net_profit_usdt']==0
    await asyncio.gather(writer(),reader())


async def test_report_api_access_exact_decimals_filter_and_drilldown(finance):
    client=(await finance['app'].clients.create_client(key='client',actor_id=finance['actor'],name='Customer'))['client_id']
    operation=await finance['app'].wallets.deposit(**common(finance,'deposit'),client_id=client,instrument='USDT',quantity='1.123456',quoted_rate='1')
    app=create_thai_finance_api(container=finance['app'],jwt_secret='x'*32)
    token=create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as http:
        assert (await http.get('/api/v1/session')).status_code==401
        http.headers['Authorization']='Bearer '+token
        user=(await http.get('/api/v1/session')).json()
        assert 'terms.manage' in user['permissions'] and user['display_name']=='Manager'
        assert len((await http.get('/api/v1/instruments')).json())==8
        assert (await http.get(f'/api/v1/clients/{client}')).json()['name']=='Customer'
        data=(await http.get('/api/v1/reports/dashboard',params={'period':str(PERIOD)})).json()
        assert Decimal(next(r['balance'] for r in data['cash'] if r['instrument_code']=='USDT'))==Decimal('1.123456')
        found=(await http.get('/api/v1/reports/operations',params={'account_id':finance['cash']['USDT'],'period':str(PERIOD),'currency':'USDT','limit':1})).json()
        assert found['items'][0]['id']==operation.id
        assert found['next_before_id']==operation.id
        lines = (await http.get(f'/api/v1/finance/operations/{operation.id}')).json()['lines']
        assert lines and all(line['purpose'] and line['account_code'] for line in lines)
        assert any(Decimal(line['quoted_rate']) == 1 for line in lines if line['quoted_rate'])
        assert (await http.get('/api/v1/reports/operations',params={'currency':'EUR'})).json()['items']==[]
        assert (await http.get('/api/v1/reports/dashboard',params={'period':str(DAY)})).status_code==422


async def test_clients_workspace_uses_live_wallets_groups_and_historical_turnover(finance):
    from app.thai_telegram import ThaiTelegramCommands
    from tests.thai.test_posting import INSTANT

    commands = ThaiTelegramCommands(finance['app'], finance['config'])
    counter = 0

    async def send(text):
        nonlocal counter
        counter += 1
        return await commands.execute(telegram_user_id=12345, chat_id=-123, message_id=counter,
                                      text=text, effective_at=INSTANT, chat_name='Клиентский чат')

    await send('/start')
    client_id = await finance['app'].trading_queries.client_by_chat(-123)
    await send('/курс бат 35')
    await send('/пбат 3500 от 100')
    await send('/курс бат 40')  # Directory keeps original posted valuation.
    await finance['app'].clients.assign_group(key='partner', actor_id=finance['actor'], client_id=client_id,
                                               group_code='partner', reason='Партнер')
    row = (await finance['app'].client_queries.workspace())['clients'][0]
    assert row['name'] == 'Клиентский чат' and row['group_name'] == 'Партнер'
    assert row['telegram_chat_id'] == -123
    assert len(row['wallets']) == 8
    assert next(w['balance'] for w in row['wallets'] if w['instrument_code']=='THB_CASH') == -3500
    assert row['stats']['turnover_usdt'] == 100
    assert row['stats']['sale_usdt'] == 100
    assert row['stats']['operations_count'] == 1
    assert row['recent'][0]['kind'] == 'sale'
    assert row['recent'][0]['href'].startswith('/deals/')
    await send('/отмена 1 Проверка')
    row = (await finance['app'].client_queries.workspace(client_id))['clients'][0]
    assert row['stats'] == {}  # Cancelled exchange excluded from statistics.
    assert row['recent'][0]['status'] == 'cancelled'
    assert (await finance['app'].client_queries.workspace(client_id+100))['clients'] == []


async def test_client_directory_api_create_chat_invite_validation_and_permissions(finance):
    app = create_thai_finance_api(container=finance['app'], jwt_secret='x'*32)
    token = create_access_token(tg_user_id=12345, secret='x'*32, ttl_seconds=60)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
        assert (await http.get('/api/v1/clients/workspace')).status_code == 401
        http.headers['Authorization'] = 'Bearer '+token
        payload = {'idempotency_key':'directory:create', 'name':'Клиент', 'chat_id':-555, 'group_code':'balance'}
        result = await http.post('/api/v1/clients', json=payload)
        assert result.status_code == 200
        client_id = result.json()['client_id']
        assert (await http.post('/api/v1/clients', json=payload)).json() == result.json()
        endpoint = f'/api/v1/clients/{client_id}/telegram-link'
        request = {'idempotency_key':'invite', 'invite_link':'https://t.me/+example'}
        result = await http.put(endpoint, json=request)
        assert result.status_code == 200
        assert (await http.put(endpoint, json=request)).json() == result.json()
        data = (await http.get('/api/v1/clients/workspace')).json()
        assert data['clients'][0]['invite_link'] == 'https://t.me/+example'
        assert data['clients'][0]['telegram_chat_id'] == -555
        for index, value in enumerate(['https://evil.test/chat', 'javascript:alert(1)', 'https://t.me@evil.test/chat']):
            result = await http.put(endpoint, json={'idempotency_key':f'invalid:{index}', 'invite_link':value})
            assert result.status_code == 422
        result = await http.put(endpoint, json={'idempotency_key':'clear', 'invite_link':None})
        assert result.status_code == 200
        assert (await http.get('/api/v1/clients/workspace')).json()['clients'][0]['invite_link'] is None
        await finance['pool'].execute("DELETE FROM user_roles WHERE user_id=$1", finance['actor'])
        await finance['pool'].execute("INSERT INTO user_roles VALUES ($1,'cashier')", finance['actor'])
        assert (await http.put(endpoint, json={'idempotency_key':'forbidden', 'invite_link':'https://t.me/example'})).status_code == 403
