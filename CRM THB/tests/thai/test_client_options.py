"""The client picker and the lookup by ID share the current Thai directory."""

from httpx import ASGITransport, AsyncClient

from api.app import create_api_app
from api.auth import create_access_token


async def test_client_options_preserve_duplicate_names_and_scope_the_card(finance):
    container = finance['app']
    config = finance['config']
    ids = []
    for group in ('balance', 'partner', 'balance'):
        client = await container.clients.create_client(
            key=f'client-{len(ids)}', actor_id=finance['actor'], name='Same name', group_code=group,
        )
        ids.append(client['client_id'])
    await container.clients.close(key='close', actor_id=finance['actor'], client_id=ids[2], reason='Test')
    await finance['pool'].execute(
        "INSERT INTO users(display_name,telegram_user_id) VALUES ('No permissions',999)"
    )

    app = create_api_app(config, container=container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test/api/v1') as http:
        assert (await http.get('clients/options')).status_code == 401
        denied = create_access_token(tg_user_id=999, secret=config.jwt_secret, ttl_seconds=60)
        assert (await http.get('clients/options', headers={'Authorization': f'Bearer {denied}'})).status_code == 403

        actor = create_access_token(tg_user_id=12345, secret=config.jwt_secret, ttl_seconds=60)
        http.headers['Authorization'] = f'Bearer {actor}'
        options = (await http.get('clients/options')).json()
        assert options == [{'id': ids[0], 'name': 'Same name'}, {'id': ids[1], 'name': 'Same name'}]
        assert (await http.get('clients/options', params={'group_code': 'partner'})).json() == options[1:]

        params = {'client_id': ids[1]}
        card = (await http.get('clients/workspace', params=params)).json()
        directory = (await http.get('clients/workspace')).json()['clients']
        assert card == {'clients': [next(row for row in directory if row['id'] == ids[1])]}
        assert len(card['clients'][0]['wallets']) == 8
        assert (await http.get('clients/workspace', params={'client_id': 9999})).json() == {'clients': []}
