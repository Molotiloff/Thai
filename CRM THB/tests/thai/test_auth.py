from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

from api import thai_auth
from api.auth import AuthError, decode_access_token
from api.thai_finance import create_thai_finance_api
from config import Config

ORIGIN = "https://thai.example"
PAYLOAD = {
    "code": "provider-code",
    "code_verifier": "v" * 43,
    "nonce": "n" * 32,
    "redirect_uri": ORIGIN + "/api/auth/telegram/oidc/callback",
}


def config():
    return Config(
        "unused",
        "x" * 32,
        telegram_oidc_client_id="123",
        telegram_oidc_client_secret="thai-oidc-secret",
        frontend_origin=ORIGIN,
    )


async def test_oidc_session_authorizes_only_existing_thai_users(finance, monkeypatch):
    seen = []

    async def exchange(**kwargs):
        seen.append(kwargs)
        return 12345

    monkeypatch.setattr(thai_auth, "exchange_telegram_oidc_code", exchange)
    app = create_thai_finance_api(
        container=finance["app"], jwt_secret="x" * 32, oidc_config=config()
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post("/api/v1/auth/telegram/oidc", json=PAYLOAD)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        token = response.json()["access_token"]
        assert decode_access_token(token, secret="x" * 32).sub == "12345"
        assert response.json()["expires_in"] == 3600
        assert seen[0]["client_secret"] == "thai-oidc-secret"
        assert seen[0]["expected_nonce"] == PAYLOAD["nonce"]
        session = await http.get("/api/v1/session", headers={"Authorization": "Bearer " + token})
        assert session.status_code == 200
        await finance["pool"].execute(
            "UPDATE users SET is_active=false WHERE id=$1", finance["actor"]
        )
        assert (await http.post("/api/v1/auth/telegram/oidc", json=PAYLOAD)).status_code == 403
        assert await finance["pool"].fetchval("SELECT count(*) FROM financial_operations") == 0


async def test_oidc_rejects_unknown_user_invalid_provider_and_redirect(finance, monkeypatch):
    async def unknown(**kwargs):
        return 999999

    monkeypatch.setattr(thai_auth, "exchange_telegram_oidc_code", unknown)
    app = create_thai_finance_api(
        container=finance["app"], jwt_secret="x" * 32, oidc_config=config()
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.post("/api/v1/auth/telegram/oidc", json=PAYLOAD)).status_code == 403
        assert (
            await http.post(
                "/api/v1/auth/telegram/oidc",
                json={**PAYLOAD, "redirect_uri": "https://foreign.invalid/callback"},
            )
        ).status_code == 422
        assert (
            await http.post(
                "/api/v1/auth/telegram/oidc", json={**PAYLOAD, "code_verifier": "short"}
            )
        ).status_code == 422

        async def invalid(**kwargs):
            raise AuthError("bad signature")

        monkeypatch.setattr(thai_auth, "exchange_telegram_oidc_code", invalid)
        assert (await http.post("/api/v1/auth/telegram/oidc", json=PAYLOAD)).status_code == 401


@pytest.mark.parametrize("configured", [None, replace(config(), telegram_oidc_client_secret=None)])
async def test_oidc_missing_config_is_service_unavailable(finance, configured):
    app = create_thai_finance_api(
        container=finance["app"], jwt_secret="x" * 32, oidc_config=configured
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.post("/api/v1/auth/telegram/oidc", json=PAYLOAD)).status_code == 503


async def test_mgr_grants_full_crm_access_and_revokes_existing_session(finance, monkeypatch):
    async def exchange(**kwargs):
        return 777

    monkeypatch.setattr(thai_auth, 'exchange_telegram_oidc_code', exchange)
    container = finance['app']
    app = create_thai_finance_api(container=container, jwt_secret='x' * 32, oidc_config=config())
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
        assert (await http.post('/api/v1/auth/telegram/oidc', json=PAYLOAD)).status_code == 403
        await container.manager_admin.handle_command('/mgr + 777 Staff', actor_id=finance['actor'], key='mgr-add')
        login = await http.post('/api/v1/auth/telegram/oidc', json=PAYLOAD)
        assert login.status_code == 200
        headers = {'Authorization': 'Bearer ' + login.json()['access_token']}
        session = await http.get('/api/v1/session', headers=headers)
        assert session.status_code == 200
        user = session.json()
        assert user['roles'] == ['manager']
        admin_permissions = {row['permission'] for row in await finance['pool'].fetch(
            "SELECT permission FROM role_permissions WHERE role='admin'")}
        assert admin_permissions <= set(user['permissions'])
        assert {'terms.manage', 'period.revise', 'access.manage', 'cash.execute'} <= set(user['permissions'])
        payload = {'idempotency_key': 'staff-group', 'code': 'staff_test', 'name': 'Staff group'}
        assert (await http.post('/api/v1/client-groups', json=payload, headers=headers)).status_code == 200
        await container.manager_admin.handle_command('/mgr - 777', actor_id=finance['actor'], key='mgr-remove')
        assert (await http.get('/api/v1/session', headers=headers)).status_code == 403
        assert (await http.post('/api/v1/client-groups', json={**payload, 'idempotency_key': 'revoked'}, headers=headers)).status_code == 403
        assert (await http.post('/api/v1/auth/telegram/oidc', json=PAYLOAD)).status_code == 403
        await container.manager_admin.handle_command('/mgr + 777 Staff', actor_id=finance['actor'], key='mgr-readd')
        await finance['pool'].execute('UPDATE users SET is_active=false WHERE telegram_user_id=777')
        assert (await http.get('/api/v1/session', headers=headers)).status_code == 401
        assert (await http.post('/api/v1/auth/telegram/oidc', json=PAYLOAD)).status_code == 403
