"""E10 read-only environment checks and deployment health contracts."""

import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.thai_finance import create_thai_finance_api
from app.thai_maintenance import ThaiDatabaseMaintenance
from tests.thai.test_acceptance import database_contents
from tests.thai.test_monitoring_bestchange import OUR
from tests.thai.test_posting import fund
from tests.thai.test_profit import configure


def settings(finance,tmp_path,*,frontend_origin='https://thai.example'):
    config=replace(finance['config'],bot_token='private-bot-token',admin_ids=(12345,),
        telegram_oidc_client_id='123',telegram_oidc_client_secret='private-oidc-secret',
        frontend_origin='https://thai.example',usdt_wallet=OUR)
    path=tmp_path/'frontend.env'
    path.write_text(f'THAI_FRONTEND_ORIGIN={frontend_origin}\nTHAI_TELEGRAM_OIDC_CLIENT_ID=123\n'
        'THAI_API_BASE_URL=http://127.0.0.1:8001/api/v1\n')
    return config,path


def checks(result):
    return {c['code']:c['status'] for c in result['checks']}


async def test_release_check_uses_read_only_snapshot_and_redacts_secrets(finance,tmp_path):
    config,path=settings(finance,tmp_path)
    buffer,dany=await configure(finance)
    for client,chat in ((buffer,-100),(dany,-101)):
        await finance['app'].clients.bind_chat(key=f'bind:{chat}',actor_id=finance['actor'],client_id=client,chat_id=chat)
    before=await database_contents(finance['pool'])
    report=await ThaiDatabaseMaintenance(config.database_url).check(config,frontend_env=path)
    assert report['ready'],report
    assert checks(report)['operations_chat']=='warn'
    assert checks(report)['business_chats']=='pass'
    assert await database_contents(finance['pool'])==before
    public=json.dumps(report)
    for secret in (config.database_url,config.bot_token,config.jwt_secret,config.telegram_oidc_client_secret):
        assert secret not in public


async def test_release_check_blocks_domain_missing_setup_and_nonempty_new_release(finance,tmp_path):
    config,path=settings(finance,tmp_path,frontend_origin='https://old.example')
    config=replace(config,usdt_wallet=None)
    inspector=ThaiDatabaseMaintenance(config.database_url)
    report=await inspector.check(config,frontend_env=path,expect_empty=True)
    assert not report['ready']
    result=checks(report)
    assert result['origin_match']=='fail' and result['business_chats']=='fail' and result['usdt_wallet']=='fail'
    assert result['empty_release']=='pass'
    await fund(finance)
    assert checks(await inspector.check(config,frontend_env=path,expect_empty=True))['empty_release']=='fail'
    path.write_text(path.read_text()+'THAI_BOT_TOKEN=private-bot-token\n')
    assert checks(await inspector.check(config,frontend_env=path))['frontend_secrets']=='fail'


async def test_release_check_detects_projection_drift_and_stale_outbox(finance,tmp_path):
    config,path=settings(finance,tmp_path)
    await fund(finance)
    await finance['pool'].execute('UPDATE account_balances SET debit_signed_native=debit_signed_native+1 WHERE account_id=$1',finance['cash']['THB_CASH'])
    await finance['pool'].execute("UPDATE inventory_balances SET quantity=quantity+1 WHERE instrument_code='THB_CASH'")
    await finance['pool'].execute("UPDATE telegram_outbox SET status='failed',last_error='private transport token'")
    report=await ThaiDatabaseMaintenance(config.database_url).check(config,frontend_env=path)
    assert not report['ready']
    assert all(checks(report)[key]=='fail' for key in ('account_projections','cash_projections','outbox'))
    assert 'private transport token' not in json.dumps(report)
    assert checks(report)['journal']=='pass'


async def test_health_checks_expose_only_status_and_refuse_stale_schema(finance,monkeypatch):
    app=create_thai_finance_api(container=finance['app'],jwt_secret='x'*32)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as http:
        assert (await http.get('/health/live')).json()=={'status':'ok'}
        ready=await http.get('/health/ready')
        assert ready.status_code==200 and ready.json()=={'status':'ready'}
        assert ready.headers['cache-control']=='no-store'
        assert (await http.get('/api/v1/finance/cash')).status_code==401
        await finance['pool'].execute("UPDATE alembic_version_thai SET version_num='thai_0000'")
        ready=await http.get('/health/ready')
        assert ready.status_code==503 and ready.json()=={'status':'not_ready'}
        with pytest.raises(RuntimeError,match='current isolated Thai schema'):
            async with app.router.lifespan_context(app):
                pytest.fail('Stale schema must not serve API')
        monkeypatch.setattr('api.thai_finance.thai_runtime_status',AsyncMock(side_effect=OSError('private DSN')))
        assert (await http.get('/health/ready')).json()=={'status':'not_ready'}
        assert (await http.get('/health/live')).status_code==200


async def test_release_check_unavailable_database_is_safe(finance,tmp_path,monkeypatch):
    config,path=settings(finance,tmp_path)
    monkeypatch.setattr('app.thai_maintenance.asyncpg.connect',AsyncMock(side_effect=OSError('private DSN')))
    report=await ThaiDatabaseMaintenance(config.database_url).check(config,frontend_env=path)
    assert not report['ready'] and checks(report)['database']=='fail'
    assert 'private DSN' not in json.dumps(report)


async def test_runtime_refuses_unknown_revision_before_bootstrapping_admins(finance,monkeypatch):
    from alembic.util.exc import CommandError

    import bot_app
    before=await finance['pool'].fetchval('SELECT count(*) FROM users')
    await finance['pool'].execute("UPDATE alembic_version_thai SET version_num='thai_0000'")
    monkeypatch.setattr(bot_app,'bootstrap_admins',AsyncMock(side_effect=AssertionError('Must not bootstrap')))
    with pytest.raises(CommandError,match='thai_0000'):
        await bot_app.BotApp(finance['config'],api_only=True).run()
    assert await finance['pool'].fetchval('SELECT count(*) FROM users')==before


async def test_http_smoke_bypasses_system_proxy_and_never_completes_login(finance,tmp_path,monkeypatch):
    import httpx
    config,path=settings(finance,tmp_path)
    seen=[]
    def respond(request):
        seen.append(request)
        if request.url.path=='/health/ready':
            return httpx.Response(200,json={'status':'ready'})
        if request.url.path=='/login':
            return httpx.Response(200,text='Login')
        assert request.headers['host']=='thai.example'
        return httpx.Response(307,headers={'Location':'https://oauth.telegram.org/auth?state=private'})
    original=httpx.AsyncClient
    def factory(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original(transport=httpx.MockTransport(respond),**kwargs)
    monkeypatch.setattr('app.thai_maintenance.httpx.AsyncClient',factory)
    inspector=ThaiDatabaseMaintenance(config.database_url)
    result=await inspector.check(config,frontend_env=path,frontend_url='http://127.0.0.1:3001')
    assert all(checks(result)[key]=='pass' for key in ('api_http','frontend_http','oidc_http'))
    assert len(seen)==3 and all(r.method=='GET' for r in seen)
    assert all(r.url.host=='127.0.0.1' for r in seen)
    assert 'private' not in json.dumps(result)
    seen.clear()
    result=await inspector.check(config,frontend_url='https://external.example')
    assert checks(result)['http_target']=='fail' and not seen
    invalid=replace(config,frontend_origin='https://user:private@thai.example')
    result=await inspector.check(invalid,frontend_url='http://127.0.0.1:3001')
    assert checks(result)['oidc_http']=='fail' and len(seen)==2
    assert all('private' not in str(r.headers) for r in seen)
