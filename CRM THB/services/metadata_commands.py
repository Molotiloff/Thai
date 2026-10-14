import hashlib
from typing import cast

from domain.finance.models import IdempotencyConflict, InvalidPosting, canonical_json
from services.clients.ports import ClientUnitOfWork
from services.finance.ports import FinanceUowFactory


class MetadataCommandService:
    """Shared transactional audit/idempotency for application commands."""

    def __init__(self, uow_factory: FinanceUowFactory):
        self._factory = uow_factory

    async def execute(self, *, key, actor_id, kind, payload, action, permission="client.manage", work=None):
        if not key.strip() or len(key) > 200:
            raise InvalidPosting("Нужен ключ команды длиной до 200 символов")
        fingerprint = hashlib.sha256(
            canonical_json({"actor": actor_id, "kind": kind, **payload}).encode()
        ).hexdigest()
        async def apply(transaction):
            uow = cast(ClientUnitOfWork, transaction)
            await uow.clients.guard_directory(actor_id, key, permission)
            previous = await uow.clients.find_event(key)
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise IdempotencyConflict("Ключ CRM уже использован для другой команды")
                return previous["result"]
            result = await action(uow)
            await uow.clients.save_event(key, fingerprint, kind, actor_id, payload, result)
            return result

        if work is not None:
            return await apply(work)
        async with self._factory() as transaction:
            result = await apply(transaction)
            await transaction.commit()
            return result
