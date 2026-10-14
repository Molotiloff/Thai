from datetime import UTC, date, datetime

from fastapi import Depends, Query
from pydantic import BaseModel, Field, StrictStr

from api.thai_capital import CommandRequest
from api.thai_clients import DirectoryRequest
from api.thai_common import business_day, operation_response, response
from db_asyncpg.thai_trading.workspace import DealWorkspaceQueries
from domain.finance.models import InvalidPosting
from services.cash_requests.parsing import ParsedRequest


class Leg(BaseModel):
    instrument: str
    quantity: StrictStr
    quoted_rate: StrictStr | None = None


class TradeRequest(DirectoryRequest):
    client_id: int | None = Field(None, gt=0)
    mode: str
    receive: Leg
    pay: Leg
    external_reference: str | None = None
    effective_at: datetime | None = None
    business_date: date | None = None
    profit_revision_id: int | None = Field(None,gt=0)
    comment: str = ''


class PostRequest(CommandRequest):
    profit_revision_id: int | None = Field(None, gt=0)


class CancelRequest(PostRequest):
    reason: str = Field(min_length=1)
    replacement: TradeRequest | None = None


class QuoteRequest(CommandRequest):
    instrument: str
    quoted_rate: StrictStr


class TransferRequest(PostRequest):
    client_id: int = Field(gt=0)
    source_instrument: str
    target_instrument: str
    quantity: StrictStr
    fee: StrictStr = "0"
    quoted_rate: StrictStr
    physical: bool = False


class WatchRequest(DirectoryRequest):
    chat_id: int
    address: str = Field(min_length=1)
    our_address: str = Field(min_length=1)
    timeout_at: datetime


class CashRequestInput(DirectoryRequest):
    client_id: int = Field(gt=0)
    kind: str
    instrument: str | None = None
    quantity: StrictStr | None = None
    receive: Leg | None = None
    pay: Leg | None = None
    contact1: str = ''
    contact2: str = ''
    comment: str = ''


class CashRequestEdit(DirectoryRequest):
    quantity: StrictStr | None = None
    receive_quantity: StrictStr | None = None
    pay_quantity: StrictStr | None = None
    contact1: str = ''
    contact2: str = ''
    comment: str = ''


class CashRequestSettlement(PostRequest):
    effective_at: datetime | None = None
    instrument: str
    signed_quantity: StrictStr
    quoted_rate: StrictStr | None = None


class ExchangeReplacement(BaseModel):
    mode: str
    receive: Leg
    pay: Leg
    comment: str = ''


class CorrectionRequest(PostRequest):
    replacement: ExchangeReplacement
    expected_deal_id: int = Field(gt=0)
    reason: str = Field(min_length=1)


def register_trading_routes(app, container, actor):
    @app.get('/api/v1/payment-watches/{watch_id}')
    async def payment_watch_detail(watch_id: int,user: int = Depends(actor)):
        return response(await container.trading_queries.payment_watch(watch_id))

    @app.get('/api/v1/deals/workspace')
    async def workspace(query: str = Query('',max_length=200), status: str | None = None,
                        kind: str | None = None, before: str | None = None,
                        limit: int = Query(50,ge=1,le=100), user: int = Depends(actor)):
        return response(await DealWorkspaceQueries(container.pool).workspace(
            query=query,status=status,kind=kind,before=before,limit=limit))

    def common(payload, user):
        return {
            "key": payload.idempotency_key,
            "actor_id": user,
            "business_date": business_day(payload.effective_at, payload.business_date),
            "effective_at": payload.effective_at,
        }

    @app.post('/api/v1/cash-requests')
    async def create_cash(payload: CashRequestInput, user: int = Depends(actor)):
        if payload.kind not in ('deposit','withdrawal','cash_exchange'):
            raise InvalidPosting('Неизвестный тип кассовой заявки')
        client_name = await container.pool.fetchval(
            'SELECT p.name FROM clients c JOIN parties p ON p.id=c.party_id WHERE c.id=$1', payload.client_id)
        creator_name = await container.pool.fetchval('SELECT display_name FROM users WHERE id=$1',user)
        contacts = {'contact1':payload.contact1,'contact2':payload.contact2,'comment':payload.comment}
        rates = None
        if payload.kind=='cash_exchange':
            if payload.receive is None or payload.pay is None:
                raise InvalidPosting('Укажите обе стороны кассового обмена')
            parsed = ParsedRequest(cmd='',kind='fx',city='',in_code=payload.receive.instrument,
                out_code=payload.pay.instrument,amt_in_expr=payload.receive.quantity,amt_out_expr=payload.pay.quantity,**contacts)
            rates = {'receive':payload.receive.quoted_rate,'pay':payload.pay.quoted_rate}
        else:
            if payload.instrument is None or payload.quantity is None:
                raise InvalidPosting('Укажите валюту и сумму кассовой заявки')
            parsed = ParsedRequest(cmd='', kind='dep' if payload.kind=='deposit' else 'wd', city='',
                code=payload.instrument, amount_expr=payload.quantity,**contacts)
        result = await container.cash_requests.create_request(key=payload.idempotency_key, actor_id=user,
            client_id=payload.client_id, parsed=parsed, client_name=client_name, creator_name=creator_name,quoted_rates=rates)
        return response({'deal_id':result['id'], 'request_id':f"Б-{result['id']:06d}",
            'external_number':f"Б-{result['id']:06d}", 'status':result['effective_status']})

    @app.put('/api/v1/cash-requests/{deal_id}')
    async def edit_cash(deal_id: int, payload: CashRequestEdit, user: int = Depends(actor)):
        deal = await container.trading_queries.detail(deal_id)
        if deal['kind'] not in ('deposit','withdrawal','cash_exchange'):
            raise InvalidPosting('Это не кассовая заявка')
        contacts = {'contact1':payload.contact1,'contact2':payload.contact2,'comment':payload.comment}
        if deal['kind']=='cash_exchange':
            if payload.receive_quantity is None or payload.pay_quantity is None:
                raise InvalidPosting('Укажите обе суммы кассового обмена')
            parsed = ParsedRequest(cmd='',kind='fx',city='',in_code=deal['attributes']['receive']['instrument'],
                out_code=deal['attributes']['pay']['instrument'],amt_in_expr=payload.receive_quantity,
                amt_out_expr=payload.pay_quantity,**contacts)
        else:
            if payload.quantity is None:
                raise InvalidPosting('Укажите сумму заявки')
            parsed = ParsedRequest(cmd='', kind='dep' if deal['kind']=='deposit' else 'wd', city='',
                code=deal['attributes']['instrument'], amount_expr=payload.quantity,**contacts)
        result = await container.cash_requests.edit_request(key=payload.idempotency_key, actor_id=user,
            request_id=f'Б-{deal_id:06d}', parsed=parsed, editor_name=await container.pool.fetchval(
                'SELECT display_name FROM users WHERE id=$1',user), effective_at=datetime.now(UTC))
        return response({'deal_id':result['id'], 'status':result['effective_status']})

    @app.post('/api/v1/cash-requests/{deal_id}/settle')
    async def settle_cash(deal_id: int, payload: CashRequestSettlement, user: int = Depends(actor)):
        now=datetime.now(UTC)
        result = await container.cash_requests.settle_request(
            key=payload.idempotency_key,actor_id=user,effective_at=now,business_date=business_day(now,None),
            request_id=f'Б-{deal_id:06d}', instrument=payload.instrument,
            signed_quantity=payload.signed_quantity, quoted_rate=payload.quoted_rate,profit_revision_id=payload.profit_revision_id)
        return response(result) if isinstance(result,dict) else operation_response(result)

    @app.get("/api/v1/deals")
    async def deals(
        client_id: int | None = Query(None, gt=0),
        before_id: int | None = Query(None, gt=0),
        limit: int = Query(50, ge=1, le=100),
        user: int = Depends(actor),
    ):
        items = await container.trading_queries.list(before_id, limit, client_id)
        return response(
            {"items": items, "next_before_id": items[-1]["id"] if len(items) == limit else None}
        )

    @app.post("/api/v1/deals")
    async def create(payload: TradeRequest, user: int = Depends(actor)):
        if payload.mode=='cash' and payload.client_id is not None:
            return await create_cash(CashRequestInput(idempotency_key=payload.idempotency_key,
                client_id=payload.client_id,kind='cash_exchange',receive=payload.receive,pay=payload.pay,
                comment=payload.comment),user)
        if payload.mode=='wallet':
            now=payload.effective_at or datetime.now(UTC)
            previous=await container.trading_queries.exchange_by_reference(payload.idempotency_key)
            if previous is not None and previous['operation_id'] is not None and payload.effective_at is None:
                now=await container.pool.fetchval('SELECT effective_at FROM financial_operations WHERE id=$1',previous['operation_id'])
            context=await container.exchange_requests.create_from_crm(
                client_id=payload.client_id,receive=payload.receive.model_dump(),pay=payload.pay.model_dump(),
                common={'key':payload.idempotency_key,'actor_id':user,'effective_at':now,
                    'business_date':business_day(now,payload.business_date)},
                client_name=await container.pool.fetchval(
                    'SELECT p.name FROM clients c JOIN parties p ON p.id=c.party_id WHERE c.id=$1',payload.client_id),
                actor_name=await container.pool.fetchval('SELECT display_name FROM users WHERE id=$1',user),
                comment=payload.comment,profit_revision_id=payload.profit_revision_id)
            return response({'deal_id':context['root']['id'],'request_id':str(context['root']['id']),
                'external_number':str(context['root']['id']),'operation_id':context['active']['operation_id'],
                'status':context['active']['effective_status'],'notification_queued':True})
        created=await container.trading.create(
                key=payload.idempotency_key,
                actor_id=user,
                client_id=payload.client_id,
                mode=payload.mode,
                receive=payload.receive.model_dump(),
                pay=payload.pay.model_dump(),
                external_reference=payload.external_reference,
            )
        return response({**created,'request_id':str(created['deal_id']),'external_number':str(created['deal_id'])})

    @app.get("/api/v1/deals/{deal_id}")
    async def detail(deal_id: int, user: int = Depends(actor)):
        deal = await container.trading_queries.detail(deal_id)
        if deal['kind']=='fx_trade':
            context = await container.trading_queries.exchange_context(deal_id)
            deal = {**deal,'request_id':context['root']['id'],'external_number':str(context['root']['id']),
                'current_deal_id':context['active']['id']}
        else:
            deal={**deal,'external_number':f'Б-{deal_id:06d}' if deal['kind'] in ('deposit','withdrawal','cash_exchange') else str(deal_id)}
        return response(deal)

    @app.post("/api/v1/deals/{deal_id}/ready")
    async def ready(deal_id: int, payload: DirectoryRequest, user: int = Depends(actor)):
        deal = await container.trading_queries.detail(deal_id)
        if deal['kind'] in ('deposit','withdrawal','cash_exchange'):
            return response(await container.cash_requests.change_request_status(key=payload.idempotency_key,
                actor_id=user, request_id=f'Б-{deal_id:06d}', action='ready'))
        return response(
            await container.trading.ready(
                key=payload.idempotency_key, actor_id=user, deal_id=deal_id
            )
        )

    @app.post("/api/v1/deals/{deal_id}/post")
    async def post(deal_id: int, payload: PostRequest, user: int = Depends(actor)):
        return operation_response(
            await container.trading.post(**common(payload, user), deal_id=deal_id, profit_revision_id=payload.profit_revision_id)
        )

    @app.post('/api/v1/deals/{deal_id}/correction')
    async def correct(deal_id: int, payload: CorrectionRequest, user: int = Depends(actor)):
        return response(await container.exchange_requests.correct_accounting(
            common=common(payload,user),deal_id=deal_id,replacement=payload.replacement.model_dump(),
            expected_deal_id=payload.expected_deal_id,reason=payload.reason,profit_revision_id=payload.profit_revision_id,
            actor_name=await container.pool.fetchval('SELECT display_name FROM users WHERE id=$1',user)))

    @app.post("/api/v1/deals/{deal_id}/cancel")
    async def cancel(deal_id: int, payload: CancelRequest, user: int = Depends(actor)):
        deal = await container.trading_queries.detail(deal_id)
        if deal['kind'] in ('deposit','withdrawal','cash_exchange') and deal['operation_id'] is None:
            return response(await container.cash_requests.change_request_status(key=payload.idempotency_key,
                actor_id=user, request_id=f'Б-{deal_id:06d}', action='cancel', reason=payload.reason))
        if deal["operation_id"] is None:
            return response(
                await container.trading.cancel_draft(
                    key=payload.idempotency_key,
                    actor_id=user,
                    deal_id=deal_id,
                    reason=payload.reason,
                )
            )
        replacement = (
            None
            if payload.replacement is None
            else payload.replacement.model_dump(include={"mode", "receive", "pay"})
        )
        return operation_response(
            await container.trading.reverse(
                **common(payload, user),
                deal_id=deal_id,
                reason=payload.reason,
                replacement=replacement,
                profit_revision_id=payload.profit_revision_id,
            )
        )

    @app.get("/api/v1/rates")
    async def rates(user: int = Depends(actor)):
        return response(await container.trading_queries.rates())

    @app.post("/api/v1/rates")
    async def set_rate(payload: QuoteRequest, user: int = Depends(actor)):
        return operation_response(
            await container.trading.set_rate(
                **common(payload, user),
                instrument=payload.instrument,
                quoted_rate=payload.quoted_rate,
            )
        )

    @app.post("/api/v1/wallet-transfers")
    async def transfer(payload: TransferRequest, user: int = Depends(actor)):
        args = payload.model_dump(exclude={"idempotency_key", "business_date", "effective_at"})
        return operation_response(
            await container.conversions.transfer(**common(payload, user), **args)
        )

    @app.post("/api/v1/deals/{deal_id}/payment-watch")
    async def watch(deal_id: int, payload: WatchRequest, user: int = Depends(actor)):
        return response(
            await container.trading.watch(
                key=payload.idempotency_key,
                actor_id=user,
                deal_id=deal_id,
                chat_id=payload.chat_id,
                address=payload.address,
                our_address=payload.our_address,
                timeout_at=payload.timeout_at,
            )
        )
