from __future__ import annotations

import logging
from dataclasses import dataclass, field

from aiogram import Bot

from config import Config
from services.lifecycle import (
    AsyncLifecycle,
    LifecycleComponent,
    LifecycleSupervisor,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class BotRuntimeServices:
    daily_balances_scheduler: AsyncLifecycle | None = None
    best_change_month_report_scheduler: AsyncLifecycle | None = None
    profit_capitalization_scheduler: AsyncLifecycle | None = None
    market_ws_service: AsyncLifecycle | None = None
    orderbook_service: AsyncLifecycle | None = None
    aml_queue_service: AsyncLifecycle | None = None
    payment_watch_poller: AsyncLifecycle | None = None
    tg_outbox_worker: AsyncLifecycle | None = None
    message_archive: AsyncLifecycle | None = None
    client_invite_link_backfill: AsyncLifecycle | None = None
    payment_watch_gateway: object | None = None
    _supervisor: LifecycleSupervisor | None = field(default=None, init=False)

    async def start(self, *, bot: Bot, config: Config) -> None:
        del bot
        if self._supervisor and self._supervisor.running:
            return
        if self.orderbook_service:
            await self.orderbook_service.restore_live_message(
                admin_chat_id=config.admin_chat_id,
            )
            log.info("Rapira live orderbook message restored")
        supervisor = LifecycleSupervisor(self._lifecycle_components())
        self._supervisor = supervisor
        try:
            await supervisor.start()
        except BaseException:
            self._supervisor = None
            raise

    async def stop(self) -> None:
        supervisor = self._supervisor
        try:
            if supervisor is not None:
                await supervisor.stop()
        finally:
            self._supervisor = None
            if self.payment_watch_gateway is not None:
                await self.payment_watch_gateway.aclose()
                self.payment_watch_gateway = None

    def _lifecycle_components(self) -> tuple[LifecycleComponent, ...]:
        candidates: tuple[tuple[str, AsyncLifecycle | None], ...] = (
            ("Daily balances scheduler", self.daily_balances_scheduler),
            (
                "BestChange month report scheduler",
                self.best_change_month_report_scheduler,
            ),
            ("Profit capitalization scheduler", self.profit_capitalization_scheduler),
            ("Payment watch poller", self.payment_watch_poller),
            ("AML queue service", self.aml_queue_service),
            ("Telegram outbox worker", self.tg_outbox_worker),
            ("Message archive", self.message_archive),
            ("Client invite-link backfill", self.client_invite_link_backfill),
            ("Rapira websocket service", self.market_ws_service),
        )
        return tuple(
            LifecycleComponent(name, component) for name, component in candidates if component
        )
