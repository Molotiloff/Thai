"""Thai direction names change at the read boundary, never in posted contracts."""
from decimal import Decimal

from httpx import ASGITransport, AsyncClient

from api.auth import create_access_token
from api.thai_finance import create_thai_finance_api
from db_asyncpg.thai_trading.workspace import DealWorkspaceQueries
from tests.thai.test_exchange_history import client, exchange
from tests.thai.test_posting import INSTANT, fund


async def financial_snapshot(finance):
    result = {}
    for table in ('deals','deal_legs','financial_operations','journal_lines','account_balances','inventory_movements'):
        result[table] = await finance['pool'].fetchval(f'''
            SELECT md5(coalesce(string_agg(data,'' ORDER BY data),''))
            FROM (SELECT to_jsonb(t)::text AS data FROM {table} t) s
        ''')
    return result


async def test_historical_labels_filters_and_client_statistics_do_not_change_accounting(finance):
    cid = await client(finance,'history')
    bought = await exchange(finance,'bought',cid,('THB_CASH','33600'),('USDT','1000'))
    sold = await exchange(finance,'sold',cid,('USDT','400'),('THB_CASH','13240'))
    before = await financial_snapshot(finance)
    queries = finance['app'].trading_queries
    assert (await queries.detail(bought))['attributes']['operation_type'] == 'sale'
    assert (await queries.detail(sold))['attributes']['operation_type'] == 'purchase'
    assert (await queries.detail(bought))['trade_type'] == 'purchase'
    assert (await queries.detail(bought))['trade_label'] == 'Покупка'
    assert (await queries.detail(sold))['trade_type'] == 'sale'
    assert (await queries.detail(sold))['trade_label'] == 'Продажа'
    workspace = DealWorkspaceQueries(finance['pool'])
    assert [r['deal_id'] for r in (await workspace.workspace(kind='sale'))['items']] == [sold]
    assert [r['deal_id'] for r in (await workspace.workspace(kind='purchase'))['items']] == [bought]
    customer = (await finance['app'].client_queries.workspace(cid))['clients'][0]
    assert customer['stats']['purchase_usdt'] == Decimal('1000')
    assert customer['stats']['sale_usdt'] == Decimal('400')
    assert {r['deal_id']:r['kind'] for r in customer['recent']} == {bought:'purchase',sold:'sale'}
    app = create_thai_finance_api(container=finance['app'],jwt_secret='x'*32)
    headers = {'Authorization':'Bearer '+create_access_token(tg_user_id=12345,secret='x'*32,ttl_seconds=60)}
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers=headers) as http:
        data = await http.get('/api/v1/deals/workspace',params={'kind':'sale'})
        assert data.status_code == 200
        assert [r['deal_id'] for r in data.json()['items']] == [sold]
        detail = await http.get(f'/api/v1/deals/{sold}')
        assert detail.json()['trade_label'] == 'Продажа'
        assert detail.json()['attributes']['operation_type'] == 'purchase'
    assert await financial_snapshot(finance) == before


async def test_bestchange_words_and_results_follow_baht_direction(finance):
    from app.thai_telegram import ThaiTelegramCommands
    from tests.thai.test_profit import configure
    await configure(finance)
    await fund(finance,instrument='USDT',quantity=Decimal('1000'))
    commands = ThaiTelegramCommands(finance['app'],finance['config'])
    # Existing financial BestChange codes stay USDT-oriented. Russian words are THB-oriented.
    async def send(text,mid):
        return await commands.execute(telegram_user_id=12345,chat_id=-123,message_id=mid,
            text=text,effective_at=INSTANT,chat_name='Client')
    bought = await send('/бест покупка 100 35 36 бат',1)
    assert 'Покупка батов' in bought
    first = await finance['pool'].fetchrow("SELECT attributes FROM deals WHERE kind='best_change' ORDER BY id DESC LIMIT 1")
    import json
    attrs = json.loads(first['attributes'])
    assert attrs['receive']['instrument'] == 'THB_CASH'
    assert attrs['pay']['instrument'] == 'USDT'
    sold = await send('/бест продажа 50 35 34 бат',2)
    assert 'Продажа батов' in sold
    last = await finance['pool'].fetchrow("SELECT attributes FROM deals WHERE kind='best_change' ORDER BY id DESC LIMIT 1")
    attrs = json.loads(last['attributes'])
    assert attrs['receive']['instrument'] == 'USDT'
    assert attrs['pay']['instrument'] == 'THB_CASH'
    assert await send('/бест продажа 50 35 34 бат',2) == sold
