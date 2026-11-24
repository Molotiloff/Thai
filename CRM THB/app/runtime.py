from __future__ import annotations

from dataclasses import dataclass, field

from services.lifecycle import (
    AsyncLifecycle,
    LifecycleComponent,
    LifecycleSupervisor,
)


@dataclass(slots=True)
class BotRuntimeServices:
    aml_queue_service: AsyncLifecycle | None = None
    payment_watch_poller: AsyncLifecycle | None = None
    tg_outbox_worker: AsyncLifecycle | None = None
    message_archive: AsyncLifecycle | None = None
    payment_watch_gateway: object | None = None
    _supervisor: LifecycleSupervisor | None = field(default=None, init=False)

    async def start(self) -> None:
        if self._supervisor and self._supervisor.running:
            return
        candidates = (
            ("Payment watch poller", self.payment_watch_poller),
            ("AML queue service", self.aml_queue_service),
            ("Telegram outbox worker", self.tg_outbox_worker),
            ("Message archive", self.message_archive),
        )
        supervisor = LifecycleSupervisor(
            tuple(LifecycleComponent(name, component) for name, component in candidates if component)
        )
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

