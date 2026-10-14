from datetime import date

from fastapi import Depends, Query
from pydantic import BaseModel, Field, StrictStr

from api.thai_capital import CommandRequest
from api.thai_common import business_day, operation_response, response
from domain.accounting_policy import calculate_best_change
from services.expenses.service import month


class BindingRequest(CommandRequest):
    buffer_client_id: int = Field(gt=0)
    daniyar_client_id: int = Field(gt=0)


class BestChangePreviewRequest(BaseModel):
    operation: str
    quantity: StrictStr
    market_rate: StrictStr
    client_rate: StrictStr


class BestChangeRequest(CommandRequest):
    operation: str
    quantity: StrictStr
    market_rate: StrictStr
    client_rate: StrictStr
    thb_instrument: str = "THB_CASH"
    mode: str = "cash"
    client_id: int | None = Field(None, gt=0)
    external_reference: str | None = None
    correction_of: int | None = Field(None, gt=0)
    profit_revision_id: int | None = Field(None, gt=0)


class BestChangeCorrectionRequest(CommandRequest):
    operation: str
    quantity: StrictStr
    market_rate: StrictStr
    client_rate: StrictStr
    thb_instrument: str = 'THB_CASH'
    expected_deal_id: int = Field(gt=0)
    reason: str = Field(min_length=1)
    profit_revision_id: int | None = Field(None,gt=0)


class TermRequest(CommandRequest):
    valid_from: date
    skyex_percent: StrictStr
    pe_percent: StrictStr
    reason: str = Field(min_length=1)
    profit_revision_id: int | None = Field(None, gt=0)


class CloseRequest(CommandRequest):
    period: date
    profit_revision_id: int | None = Field(None, gt=0)


class PaymentRequest(CommandRequest):
    recipient: str
    instrument: str
    quantity: StrictStr
    quoted_rate: StrictStr | None = None
    profit_revision_id: int | None = Field(None, gt=0)


class ReversalRequest(CommandRequest):
    reason: str = Field(min_length=1)
    profit_revision_id: int | None = Field(None, gt=0)


class RevisionRequest(CommandRequest):
    period: date
    reason: str = Field(min_length=1)


def register_profit_routes(app, container, actor):
    @app.post("/api/v1/best-change/preview")
    async def preview(payload: BestChangePreviewRequest, user: int = Depends(actor)):
        return response(calculate_best_change(**payload.model_dump()))

    def common(payload, user):
        return {
            "key": payload.idempotency_key,
            "actor_id": user,
            "business_date": business_day(payload.effective_at, payload.business_date),
            "effective_at": payload.effective_at,
        }

    @app.post("/api/v1/best-change/bindings")
    async def bindings(payload: BindingRequest, user: int = Depends(actor)):
        return operation_response(
            await container.best_change.configure(
                **common(payload, user),
                buffer_client_id=payload.buffer_client_id,
                daniyar_client_id=payload.daniyar_client_id,
            )
        )

    @app.get("/api/v1/best-change/bindings")
    async def current_bindings(user: int = Depends(actor)):
        return response(await container.profit_queries.optional_bindings())

    @app.post("/api/v1/best-change/deals")
    async def record(payload: BestChangeRequest, user: int = Depends(actor)):
        operation=await container.best_change.record(
                **common(payload, user),
                **payload.model_dump(exclude={"idempotency_key", "effective_at", "business_date"}),
            )
        deal_id=await container.pool.fetchval('SELECT id FROM deals WHERE operation_id=$1',operation.id)
        return operation_response(operation,deal_id=deal_id,request_id=str(deal_id),external_number=str(deal_id))

    @app.post('/api/v1/best-change/deals/{deal_id}/correct')
    async def correct(deal_id: int,payload: BestChangeCorrectionRequest,user: int = Depends(actor)):
        return response(await container.best_change.correct(**common(payload,user),deal_id=deal_id,
            expected_deal_id=payload.expected_deal_id,reason=payload.reason,
            replacement=payload.model_dump(include={'operation','quantity','market_rate','client_rate','thb_instrument'}),
            profit_revision_id=payload.profit_revision_id))

    @app.get("/api/v1/best-change/deals")
    async def deals(
        before_id: int | None = Query(None, gt=0),
        limit: int = Query(50, ge=1, le=100),
        user: int = Depends(actor),
    ):
        items = await container.profit_queries.best_change(before_id, limit)
        return response(
            {
                "items": items,
                "next_before_id": items[-1]["deal_id"] if len(items) == limit else None,
            }
        )

    @app.get("/api/v1/best-change/report")
    async def best_change_report(period: date, user: int = Depends(actor)):
        return response(await container.profit_queries.best_change_report(month(period)))

    @app.get("/api/v1/profit/report")
    async def profit_report(period: date, user: int = Depends(actor)):
        return response(await container.profit_queries.report(month(period)))

    @app.get("/api/v1/profit/closures")
    async def closure(
        period: date, revision: int | None = Query(None, gt=0), user: int = Depends(actor)
    ):
        return response(await container.profit_queries.closure(month(period), revision))

    @app.post("/api/v1/profit/close")
    async def close(payload: CloseRequest, user: int = Depends(actor)):
        business_day(payload.effective_at, payload.business_date)
        return operation_response(
            await container.profit.close(
                key=payload.idempotency_key,
                actor_id=user,
                period=payload.period,
                effective_at=payload.effective_at,
                profit_revision_id=payload.profit_revision_id,
            )
        )

    @app.get("/api/v1/profit/terms")
    async def terms(user: int = Depends(actor)):
        return response(await container.profit_queries.terms())

    @app.post("/api/v1/profit/terms")
    async def set_terms(payload: TermRequest, user: int = Depends(actor)):
        return operation_response(
            await container.profit.set_terms(
                **common(payload, user),
                **payload.model_dump(exclude={"idempotency_key", "effective_at", "business_date"}),
            )
        )

    @app.get("/api/v1/profit/obligations")
    async def obligations(user: int = Depends(actor)):
        return response(await container.profit_queries.obligations())

    @app.post("/api/v1/profit/payments")
    async def pay(payload: PaymentRequest, user: int = Depends(actor)):
        return operation_response(
            await container.profit.pay(
                **common(payload, user),
                **payload.model_dump(exclude={"idempotency_key", "effective_at", "business_date"}),
            )
        )

    @app.post("/api/v1/profit/operations/{operation_id}/reversal")
    async def reverse(operation_id: int, payload: ReversalRequest, user: int = Depends(actor)):
        return operation_response(
            await container.profit.reverse(
                **common(payload, user),
                operation_id=operation_id,
                reason=payload.reason,
                profit_revision_id=payload.profit_revision_id,
            )
        )

    @app.post("/api/v1/profit/revisions")
    async def revise(payload: RevisionRequest, user: int = Depends(actor)):
        return operation_response(
            await container.profit.start_revision(
                **common(payload, user), period=payload.period, reason=payload.reason
            )
        )

    @app.get("/api/v1/profit/revisions")
    async def revisions(period: date, user: int = Depends(actor)):
        return response(await container.profit_queries.revisions(month(period)))
