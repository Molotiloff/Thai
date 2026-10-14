from __future__ import annotations

from decimal import Decimal
from typing import Protocol


class FirmRateProvider(Protocol):
    async def get_rates(self) -> dict[str, Decimal]: ...


class DatabaseRateRepository(Protocol):
    async def latest_rub_rates(self) -> dict[str, Decimal]: ...


class DatabaseFirmRateProvider:
    """Read the current RUB-based API's stored rates until the USDT schema replaces it."""

    _crm_codes = {"USD": "USD_BL", "USDB": "USD_BL", "USDW": "USD_WH"}

    def __init__(self, repository: DatabaseRateRepository) -> None:
        self._repository = repository

    async def get_rates(self) -> dict[str, Decimal]:
        stored = await self._repository.latest_rub_rates()
        rates = {"RUB": Decimal("1")}
        for code, rate in stored.items():
            normalized = code.strip().upper()
            if normalized == "RUB" or not rate.is_finite() or rate <= 0:
                continue
            rates[self._crm_codes.get(normalized, normalized)] = rate
        return rates
