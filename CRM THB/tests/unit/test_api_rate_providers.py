from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from api.rate_providers import DatabaseFirmRateProvider


async def test_database_rate_provider_normalizes_stored_wallet_codes() -> None:
    repository = SimpleNamespace(latest_rub_rates=AsyncMock(return_value={
        "RUB": Decimal("1"), " usd ": Decimal("90"),
        "USDW": Decimal("89"), "THB": Decimal("3"),
    }))

    rates = await DatabaseFirmRateProvider(repository).get_rates()

    assert rates == {
        "RUB": Decimal("1"), "USD_BL": Decimal("90"),
        "USD_WH": Decimal("89"), "THB": Decimal("3"),
    }
    repository.latest_rub_rates.assert_awaited_once_with()


async def test_database_rate_provider_never_fabricates_missing_or_invalid_rates() -> None:
    repository = SimpleNamespace(latest_rub_rates=AsyncMock(return_value={
        "RUB": Decimal("90"), "EUR": Decimal("0"),
        "USD": Decimal("-1"), "USDT": Decimal("NaN"), "THB": Decimal("Infinity"),
    }))

    assert await DatabaseFirmRateProvider(repository).get_rates() == {"RUB": Decimal("1")}
