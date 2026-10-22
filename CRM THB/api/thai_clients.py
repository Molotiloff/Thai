from datetime import date, datetime

from fastapi import Depends, Query
from pydantic import BaseModel, Field, StrictStr

from api.thai_common import business_day, operation_response, response


class DirectoryRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)


class GroupRequest(DirectoryRequest):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=300)


class RenameRequest(DirectoryRequest):
    name: str = Field(min_length=1, max_length=300)


class ClientRequest(DirectoryRequest):
    name: str | None = Field(None, min_length=1, max_length=300)
    party_id: int | None = Field(None, gt=0)
    group_code: str = 'balance'
    chat_id: int | None = None


class AssignGroupRequest(DirectoryRequest):
    group_code: str
    reason: str = Field(min_length=1, max_length=2000)


class ChatRequest(DirectoryRequest):
    chat_id: int


class InviteRequest(DirectoryRequest):
    invite_link: str | None = Field(None, max_length=500)


class CloseRequest(DirectoryRequest):
    reason: str = Field(min_length=1)


class WalletCashRequest(DirectoryRequest):
    quantity: StrictStr
    quoted_rate: StrictStr | None = None
    effective_at: datetime
    business_date: date | None = None
    comment: str = ''


class ClientTransferRequest(WalletCashRequest):
    source_client_id: int = Field(gt=0)
    target_client_id: int = Field(gt=0)
    instrument: str
    profit_revision_id: int | None = Field(None, gt=0)


class ClientTransferAdjustmentRequest(DirectoryRequest):
    new_amount: StrictStr | None = None
    effective_at: datetime
    business_date: date | None = None
    profit_revision_id: int | None = Field(None,gt=0)


class WalletZeroRequest(DirectoryRequest):
    confirmed: bool = True
    effective_at: datetime
    profit_revision_id: int | None = Field(None,gt=0)


class WalletAdjustmentRequest(WalletCashRequest):
    profit_revision_id: int | None = Field(None,gt=0)


def register_client_routes(app, container, actor):
    @app.get('/api/v1/clients/{client_id}/statement')
    async def statement(client_id: int, since: datetime | None = None, until: datetime | None = None,
                        user: int = Depends(actor)):
        return response(await container.wallet_interactions.export_transactions(client_id=client_id,since=since,until=until))

    @app.post('/api/v1/clients/{client_id}/zero-preview')
    async def zero_preview(client_id: int,payload: DirectoryRequest,user: int = Depends(actor)):
        return response(await container.wallet_interactions.prepare_zero(key=payload.idempotency_key,actor_id=user,client_id=client_id))

    @app.post('/api/v1/clients/{client_id}/zero')
    async def zero(client_id: int,payload: WalletZeroRequest,user: int = Depends(actor)):
        from domain.finance.models import InvalidPosting
        preview = await container.client_queries.directory_event(payload.idempotency_key)
        if preview['client_id']!=client_id:
            raise InvalidPosting('Подтверждение принадлежит другому клиенту')
        return response(await container.wallet_interactions.resolve_zero(key=payload.idempotency_key,actor_id=user,
            confirmed=payload.confirmed,effective_at=payload.effective_at,business_date=business_day(payload.effective_at,None),
            profit_revision_id=payload.profit_revision_id))

    @app.post('/api/v1/clients/{client_id}/wallets/{instrument}/adjustments')
    async def wallet_adjust(client_id: int,instrument: str,payload: WalletAdjustmentRequest,user: int = Depends(actor)):
        return operation_response(await container.wallet_interactions.accounting_adjust(key=payload.idempotency_key,
            actor_id=user,client_id=client_id,instrument=instrument,quantity=payload.quantity,
            effective_at=payload.effective_at,business_date=business_day(payload.effective_at,payload.business_date),
            comment=payload.comment,profit_revision_id=payload.profit_revision_id))

    @app.get('/api/v1/clients/transfers/{operation_id}')
    async def transfer_detail(operation_id: int, user: int = Depends(actor)):
        return response(await container.client_queries.transfer_context(operation_id))

    @app.post('/api/v1/clients/transfers/{operation_id}/adjust')
    async def transfer_adjust(operation_id: int, payload: ClientTransferAdjustmentRequest,user: int = Depends(actor)):
        return response(await container.client_transfers.adjust_accounting(key=payload.idempotency_key,
            actor_id=user,operation_id=operation_id,new_amount=payload.new_amount,
            effective_at=payload.effective_at,business_date=business_day(payload.effective_at,payload.business_date),
            profit_revision_id=payload.profit_revision_id))

    @app.post('/api/v1/clients/transfers')
    async def transfer(payload: ClientTransferRequest, user: int = Depends(actor)):
        return operation_response(await container.wallet_transfers.transfer(
            key=payload.idempotency_key, actor_id=user,
            effective_at=payload.effective_at,
            business_date=business_day(payload.effective_at, payload.business_date),
            **payload.model_dump(exclude={'idempotency_key', 'effective_at', 'business_date'})))

    @app.post('/api/v1/clients/{client_id}/chat')
    async def bind(client_id: int,payload: ChatRequest,user: int = Depends(actor)):
        return response(await container.clients.bind_chat(key=payload.idempotency_key,actor_id=user,client_id=client_id,chat_id=payload.chat_id))

    @app.post('/api/v1/clients/{client_id}/close')
    async def close(client_id: int,payload: CloseRequest,user: int = Depends(actor)):
        return response(await container.clients.close(key=payload.idempotency_key,actor_id=user,client_id=client_id,reason=payload.reason))
    @app.get('/api/v1/client-groups')
    async def groups(user: int = Depends(actor)):
        return response(await container.client_queries.groups())

    @app.post('/api/v1/client-groups')
    async def create_group(payload: GroupRequest, user: int = Depends(actor)):
        return response(await container.clients.create_group(key=payload.idempotency_key, actor_id=user,
            code=payload.code, name=payload.name))

    @app.put('/api/v1/client-groups/{code}/name')
    async def rename_group(code: str, payload: RenameRequest, user: int = Depends(actor)):
        return response(await container.clients.rename_group(key=payload.idempotency_key, actor_id=user,
            code=code, name=payload.name))

    @app.get('/api/v1/clients/workspace')
    async def workspace(client_id: int | None = Query(None, gt=0), user: int = Depends(actor)):
        return response(await container.client_queries.workspace(client_id))

    @app.put('/api/v1/clients/{client_id}/telegram-link')
    async def invite(client_id: int, payload: InviteRequest, user: int = Depends(actor)):
        return response(await container.clients.set_invite_link(key=payload.idempotency_key,
            actor_id=user, client_id=client_id, invite_link=payload.invite_link))

    @app.get('/api/v1/clients')
    async def clients(after_id: int | None = Query(None, gt=0), limit: int = Query(50, ge=1, le=100), user: int = Depends(actor)):
        items = await container.client_queries.clients(after_id=after_id, limit=limit)
        return response({'items': items, 'next_after_id': items[-1]['id'] if len(items)==limit else None})

    @app.post('/api/v1/clients')
    async def create_client(payload: ClientRequest, user: int = Depends(actor)):
        return response(await container.clients.create_client(key=payload.idempotency_key, actor_id=user,
            name=payload.name, party_id=payload.party_id, group_code=payload.group_code, chat_id=payload.chat_id))

    @app.put('/api/v1/clients/{client_id}/group')
    async def assign_group(client_id: int, payload: AssignGroupRequest, user: int = Depends(actor)):
        return response(await container.clients.assign_group(key=payload.idempotency_key, actor_id=user,
            client_id=client_id, group_code=payload.group_code, reason=payload.reason))

    @app.get('/api/v1/clients/{client_id}/wallets')
    async def wallets(client_id: int, user: int = Depends(actor)):
        return response(await container.client_queries.wallets(client_id))

    @app.get('/api/v1/clients/{client_id}/wallets/{instrument}/history')
    async def history(client_id: int, instrument: str, before_id: int | None = Query(None, gt=0),
                      limit: int = Query(50, ge=1, le=100), user: int = Depends(actor)):
        items = await container.client_queries.wallet_history(client_id, instrument, before_id, limit)
        return response({'items': items, 'next_before_id': items[-1]['id'] if len(items)==limit else None})

    async def cash_change(client_id, instrument, payload, user, method):
        return operation_response(await method(key=payload.idempotency_key, actor_id=user,
            client_id=client_id, instrument=instrument, quantity=payload.quantity,
            business_date=business_day(payload.effective_at, payload.business_date), effective_at=payload.effective_at,
            comment=payload.comment))

    @app.post('/api/v1/clients/{client_id}/wallets/{instrument}/deposits')
    async def deposit(client_id: int, instrument: str, payload: WalletCashRequest, user: int = Depends(actor)):
        return await cash_change(client_id, instrument, payload, user, container.wallets.deposit)

    @app.post('/api/v1/clients/{client_id}/wallets/{instrument}/withdrawals')
    async def withdraw(client_id: int, instrument: str, payload: WalletCashRequest, user: int = Depends(actor)):
        return await cash_change(client_id, instrument, payload, user, container.wallets.withdraw)

    @app.get('/api/v1/loans')
    async def loans(client_id: int | None = Query(None, gt=0), after_client_id: int | None = Query(None, gt=0),
                    limit: int = Query(50, ge=1, le=100), include_zero: bool = False, user: int = Depends(actor)):
        return response(await container.loans.balances(client_id=client_id, after_client_id=after_client_id,
            limit=limit, include_zero=include_zero))

    @app.get('/api/v1/loans/totals')
    async def totals(client_id: int | None = Query(None, gt=0), user: int = Depends(actor)):
        return response(await container.loans.totals(client_id))

    @app.get('/api/v1/loans/{client_id}/wallets/{instrument}/history')
    async def loan_history(client_id: int, instrument: str, before_id: int | None = Query(None, gt=0),
                           limit: int = Query(50, ge=1, le=100), user: int = Depends(actor)):
        return response(await container.loans.history(client_id, instrument, before_id=before_id, limit=limit))
