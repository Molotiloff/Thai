from decimal import Decimal

import pytest

from domain.finance.models import InvalidPosting, ReversalBlocked
from tests.thai.test_posting import DAY, INSTANT, balance, fund


async def client(finance):
    return (
        await finance["app"].clients.create_client(
            key="client", actor_id=finance["actor"], name="New client"
        )
    )["client_id"]


def common(finance, key):
    return {"key": key, "actor_id": finance["actor"], "business_date": DAY, "effective_at": INSTANT}


async def trade(
    finance,
    mode="cash",
    client_id=None,
    receive="THB_CASH",
    quantity="3600",
    rate="35",
    key="trade",
):
    service = finance["app"].trading
    result = await service.create(
        key=key,
        actor_id=finance["actor"],
        client_id=client_id,
        mode=mode,
        receive={"instrument": receive, "quantity": quantity, "quoted_rate": rate},
        pay={"instrument": "USDT", "quantity": "100", "quoted_rate": "1"},
    )
    await service.ready(key=key + ":ready", actor_id=finance["actor"], deal_id=result["deal_id"])
    return result["deal_id"]


async def test_cash_trade_wac_retry_and_exact_correction(finance):
    await fund(finance, instrument="USDT", quantity=Decimal(100))
    deal = await trade(finance)
    posted = await finance["app"].trading.post(**common(finance, "post"), deal_id=deal)
    assert await finance["app"].trading.post(**common(finance, "post"), deal_id=deal) == posted
    assert await balance(finance, "USDT") == (0, 0)
    assert await balance(finance, "THB_CASH") == (3600, Decimal("102.85714286"))
    assert await finance["pool"].fetchval("SELECT count(*) FROM deal_legs") == 2
    details = await finance["app"].trading_queries.detail(deal)
    assert Decimal(details["attributes"]["result_usdt"]) == Decimal("2.85714286")
    original = details["attributes"]
    replacement = {
        "mode": "cash",
        "receive": {"instrument": "THB_CASH", "quantity": "3500", "quoted_rate": "35"},
        "pay": {"instrument": "USDT", "quantity": "100", "quoted_rate": "1"},
    }
    reversed_op = await finance["app"].trading.reverse(
        **common(finance, "reverse"), deal_id=deal, reason="Correction", replacement=replacement
    )
    assert (
        await finance["app"].trading.reverse(
            **common(finance, "reverse"), deal_id=deal, reason="Correction", replacement=replacement
        )
        == reversed_op
    )
    assert await balance(finance, "USDT") == (100, 100)
    assert await balance(finance, "THB_CASH") == (0, 0)
    corrected = await finance["app"].trading_queries.detail(deal)
    assert corrected["effective_status"] == "cancelled"
    assert corrected["attributes"] == original
    assert (await finance["app"].trading_queries.detail(corrected["replacement_deal_id"]))[
        "effective_status"
    ] == "draft"


async def test_wallet_exchange_before_cash_and_following_deposit(finance):
    cid = await client(finance)
    deal = await trade(finance, mode="wallet", client_id=cid)
    await finance["app"].trading.post(**common(finance, "post"), deal_id=deal)
    wallets = {
        row["instrument_code"]: row["balance"]
        for row in await finance["app"].client_queries.wallets(cid)
    }
    assert wallets["THB_CASH"] == -3600 and wallets["USDT"] == 100
    assert await balance(finance, "THB_CASH") == (0, 0)
    await finance["app"].wallets.deposit(
        **common(finance, "deposit"),
        client_id=cid,
        instrument="THB_CASH",
        quantity="3600",
        quoted_rate="36",
    )
    assert await balance(finance, "THB_CASH") == (3600, 100)
    with pytest.raises(ReversalBlocked):
        await finance["app"].trading.reverse(
            **common(finance, "reverse"), deal_id=deal, reason="Later movement"
        )


@pytest.mark.parametrize("physical", [False, True])
async def test_thb_conversion_fee_and_storno(finance, physical):
    cid = await client(finance)
    await finance["app"].wallets.deposit(
        **common(finance, "deposit"),
        client_id=cid,
        instrument="THB_CASH",
        quantity="7000",
        quoted_rate="35",
    )
    op = await finance["app"].conversions.transfer(
        **common(finance, "convert"),
        client_id=cid,
        source_instrument="THB_CASH",
        target_instrument="THB_BANK",
        quantity="3500",
        fee="35",
        quoted_rate="35",
        physical=physical,
    )
    wallets = {
        row["instrument_code"]: row["balance"]
        for row in await finance["app"].client_queries.wallets(cid)
    }
    assert wallets["THB_CASH"] == 3465 and wallets["THB_BANK"] == 3500
    assert await balance(finance, "THB_CASH") == ((3500, 100) if physical else (7000, 200))
    assert await balance(finance, "THB_BANK") == ((3500, 100) if physical else (0, 0))
    assert (
        await finance["pool"].fetchval(
            "SELECT b.balance FROM posted_account_balances b JOIN ledger_accounts a ON a.id=b.account_id WHERE a.purpose='transfer_fee_income'"
        )
        == 1
    )
    await finance["app"].reversals.reverse(
        **common(finance, "undo"), original_id=op.id, reason="Correction"
    )
    assert await balance(finance, "THB_CASH") == (7000, 200)


@pytest.mark.parametrize(
    "instrument,rate",
    [
        ("THB_CASH", "35"),
        ("THB_BANK", "35"),
        ("USD_WH", "1"),
        ("USD_BL", "1"),
        ("EUR", "1.1"),
        ("EUR500", "1.1"),
        ("RUB", "0.01"),
    ],
)
async def test_cash_pairs(finance, instrument, rate):
    await fund(finance, instrument="USDT", quantity=Decimal(100))
    deal = await trade(finance, receive=instrument, rate=rate, quantity="100")
    await finance["app"].trading.post(**common(finance, "post"), deal_id=deal)
    assert (await balance(finance, instrument))[0] == 100
    await finance["app"].trading.reverse(
        **common(finance, "undo"), deal_id=deal, reason="Correction"
    )
    assert await balance(finance, instrument) == (0, 0)


async def test_telegram_registration_atomic_retry_delivery_and_private_token(finance):
    from types import SimpleNamespace

    from app.thai_telegram import ThaiTelegramCommands, ThaiTelegramDelivery
    from config import Config

    commands = ThaiTelegramCommands(finance["app"], Config("unused", "x" * 32))
    request = {
        "telegram_user_id": 12345,
        "chat_id": -123,
        "message_id": 1,
        "text": "/регистрация Alice",
        "effective_at": INSTANT,
    }
    assert await commands.execute(**request) == await commands.execute(**request)
    cid = await finance["app"].trading_queries.client_by_chat(-123)
    assert await finance["pool"].fetchval("SELECT count(*) FROM clients") == 1
    await commands.execute(**{**request, "message_id": 2, "text": "/USDT 10"})
    with pytest.raises(InvalidPosting):
        await commands.execute(**{**request, "message_id": 3, "text": "/token"})

    class FakeBot:
        calls = []

        async def send_message(self, chat_id, text, **kwargs):
            self.calls.append((chat_id, text))
            return SimpleNamespace(message_id=100)

        async def edit_message_text(self, *args, **kwargs):
            return None

    bot = FakeBot()
    delivery = ThaiTelegramDelivery(finance["app"], bot)
    from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository

    event = await OutboxDeliveryRepository(finance["pool"]).claim()
    await delivery.deliver(event)
    await delivery.deliver(event)
    assert len(bot.calls) == 1
    with pytest.raises(InvalidPosting):
        await finance["app"].clients.close(
            key="close", actor_id=finance["actor"], client_id=cid, reason="Close"
        )
    with pytest.raises(InvalidPosting):
        await finance["app"].clients.create_client(
            key="conflict-chat", actor_id=finance["actor"], name="Other", chat_id=-123
        )
    assert await finance["pool"].fetchval("SELECT count(*) FROM clients") == 1


async def test_payment_watch_requires_matching_proof_and_posts_once(finance):
    from datetime import UTC, datetime, timedelta

    from services.payment_watch.models import TronTransfer
    from services.payment_watch.service import PaymentMonitor

    await fund(finance, instrument="USDT", quantity=Decimal(100))
    deal = await trade(finance)
    now = datetime.now(UTC)
    watch = await finance["app"].trading.watch(
        key="watch",
        actor_id=finance["actor"],
        deal_id=deal,
        chat_id=-123,
        address="client-address",
        our_address="our-address",
        timeout_at=now + timedelta(hours=1),
    )
    with pytest.raises(InvalidPosting):
        await finance["app"].trading.post(**common(finance, "manual"), deal_id=deal)
    block_ts = datetime.now(UTC) + timedelta(seconds=1)

    class Gateway:
        async def list_usdt_transfers(self, **kwargs):
            return [
                TronTransfer(
                    tx_hash="unique-tx",
                    from_address="our-address",
                    to_address="client-address",
                    amount=Decimal(100),
                    token_symbol="USDT",
                    block_number=100,
                    block_ts=block_ts,
                    confirmations=2,
                    confirmed=True,
                )
            ]

    monitor = PaymentMonitor(finance["app"].payment_watches, Gateway())
    await monitor.run_once()
    await monitor.run_once()
    assert (
        await finance["pool"].fetchval(
            "SELECT status FROM payment_watches WHERE id=$1", watch["watch_id"]
        )
        == "COMPLETED"
    )
    assert await finance["pool"].fetchval("SELECT count(*) FROM payment_watch_events") == 1
    assert await balance(finance, "USDT") == (0, 0)
    with pytest.raises(ReversalBlocked):
        await finance["app"].trading.reverse(
            **common(finance, "reverse"), deal_id=deal, reason="Actual refund required"
        )


async def test_database_quote_snapshots_and_closed_client_history(finance):
    cid = await client(finance)
    await finance["app"].clients.bind_chat(
        key="bind", actor_id=finance["actor"], client_id=cid, chat_id=-55
    )
    await fund(finance, instrument="USDT", quantity=Decimal(100))
    deal = await trade(finance, client_id=cid, rate=None)
    await finance["app"].trading.post(**common(finance, "post"), deal_id=deal)
    assert (await finance["app"].trading_queries.detail(deal))["attributes"]["receive"][
        "quoted_rate"
    ] == "36"
    await finance["app"].clients.close(
        key="close", actor_id=finance["actor"], client_id=cid, reason="Done"
    )
    assert await finance["app"].trading_queries.client_by_chat(-55) is None
    assert len(await finance["app"].client_queries.wallets(cid)) == 8


async def test_usdt_receipt_and_api_request_cycle(finance):
    from httpx import ASGITransport, AsyncClient

    from api.auth import create_access_token
    from api.thai_finance import create_thai_finance_api

    await fund(finance, quantity=Decimal(3500))
    app = create_thai_finance_api(container=finance["app"], jwt_secret="x" * 32)
    headers = {
        "Authorization": "Bearer "
        + create_access_token(tg_user_id=12345, secret="x" * 32, ttl_seconds=60)
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    ) as http:
        data = {
            "idempotency_key": "api-deal",
            "mode": "cash",
            "receive": {"instrument": "USDT", "quantity": "110"},
            "pay": {"instrument": "THB_CASH", "quantity": "3500", "quoted_rate": "35"},
        }
        created = await http.post("/api/v1/deals", json=data)
        assert created.status_code == 200, created.text
        deal = created.json()["deal_id"]
        assert (
            await http.post(f"/api/v1/deals/{deal}/ready", json={"idempotency_key": "api-ready"})
        ).status_code == 200
        payload = {"idempotency_key": "api-post", "effective_at": INSTANT.isoformat()}
        posted = await http.post(f"/api/v1/deals/{deal}/post", json=payload)
        assert posted.status_code == 200, posted.text
        assert (await http.post(f"/api/v1/deals/{deal}/post", json=payload)).json() == posted.json()
        assert (await http.get(f"/api/v1/deals/{deal}")).json()["attributes"]["result_usdt"] in (
            "10",
            "10.00000000",
        )
    assert await balance(finance, "USDT") == (110, 110)
    assert await balance(finance, "THB_CASH") == (0, 0)


@pytest.mark.parametrize(
    "instrument,rate",
    [
        ("THB_CASH", "35"),
        ("THB_BANK", "35"),
        ("USDT", "1"),
        ("USD_WH", "1"),
        ("USD_BL", "1"),
        ("EUR", "1.1"),
        ("EUR500", "1.1"),
        ("RUB", "0.01"),
    ],
)
async def test_wallet_cash_roundtrip_all_instruments(finance, instrument, rate):
    cid = await client(finance)
    deposit = await finance["app"].wallets.deposit(
        **common(finance, "deposit"),
        client_id=cid,
        instrument=instrument,
        quantity="100",
        quoted_rate=rate,
    )
    withdrawn = await finance["app"].wallets.withdraw(
        **common(finance, "withdraw"),
        client_id=cid,
        instrument=instrument,
        quantity="100",
        quoted_rate=rate,
    )
    assert await balance(finance, instrument) == (0, 0)
    await finance["app"].reversals.reverse(
        **common(finance, "undo-withdraw"), original_id=withdrawn.id, reason="Correction"
    )
    await finance["app"].reversals.reverse(
        **common(finance, "undo-deposit"), original_id=deposit.id, reason="Correction"
    )
    assert await balance(finance, instrument) == (0, 0)
    assert all(row["balance"] == 0 for row in await finance["app"].client_queries.wallets(cid))
