from decimal import Decimal
from typing import Protocol

from services.clients.ports import ClientUnitOfWork


class ActAccountPort(Protocol):
    async def service_amount(self, request_chat_id: int) -> Decimal: ...
    async def reconcile_service_account(self, *, request_chat_id: int, amount: Decimal,
                                        actor_id: int, key: str) -> dict: ...


class ActAccountUnitOfWork(ClientUnitOfWork, Protocol):
    act: ActAccountPort
