import asyncio
from dataclasses import dataclass
from typing import Protocol

from services.lifecycle import ManagedTaskLifecycle


class DeliveryError(Exception):
    """Delivery adapter translates recoverable transport errors to this type."""


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: int
    key: str
    event_type: str
    payload_json: str
    lease_token: str


class DeliveryPort(Protocol):
    async def deliver(self, event: OutboxEvent) -> None: ...


class DeliveryRepository(Protocol):
    async def claim(self) -> OutboxEvent | None: ...
    async def delivered(self, event: OutboxEvent) -> None: ...
    async def retry(self, event: OutboxEvent, error: str) -> None: ...


class OutboxDispatcher(ManagedTaskLifecycle):
    def __init__(self, repository: DeliveryRepository, delivery: DeliveryPort):
        super().__init__(task_name='telegram_outbox')
        self._repository = repository
        self._delivery = delivery

    async def _run(self):
        while not self.is_stopping:
            processed=await self.run_once()
            await asyncio.sleep(0.05 if processed else 1)

    async def run_once(self) -> bool:
        event = await self._repository.claim()
        if event is None:
            return False
        try:
            await self._delivery.deliver(event)
        except DeliveryError:
            # Do not store arbitrary exception text: transport errors may contain access tokens.
            await self._repository.retry(event, "delivery_failed")
        else:
            await self._repository.delivered(event)
        return True
