from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from api.presentation.dashboard import build_dashboard
from api.rate_providers import FirmRateProvider
from api.schemas.dashboard import DashboardResponse
from services.accounting.ports import AccountingDashboardReadPort


class DashboardUnavailableError(RuntimeError):
    pass


class DashboardQueryService:
    def __init__(
        self,
        *,
        dashboard_repository: AccountingDashboardReadPort,
        rate_provider: FirmRateProvider,
        today_factory: Callable[[], date] | None = None,
    ) -> None:
        self._dashboard = dashboard_repository
        self._rates = rate_provider
        self._today = today_factory or _yekaterinburg_business_date

    async def get_dashboard(self) -> DashboardResponse:
        current_date = self._today()
        try:
            snapshot = await self._dashboard.get_snapshot(business_date=current_date)
        except Exception as exc:
            raise DashboardUnavailableError(
                "Postgres dashboard snapshot is unavailable"
            ) from exc
        return build_dashboard(snapshot=snapshot, today=current_date)

    async def get_rates(self) -> dict[str, float]:
        rates = await self._rates.get_rates()
        return {code: float(rate) for code, rate in rates.items()}


def _yekaterinburg_business_date() -> date:
    return datetime.now(ZoneInfo("Asia/Yekaterinburg")).date()
