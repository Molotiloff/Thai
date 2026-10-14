from datetime import date

from fastapi import Depends, Query
from pydantic import Field, StrictStr

from api.thai_capital import CommandRequest
from api.thai_common import business_day, operation_response, response
from services.expenses.service import month


class ExpenseRequest(CommandRequest):
    profit_revision_id: int | None = Field(None, gt=0)
    category_id: int = Field(gt=0)
    instrument: str
    amount: StrictStr
    quoted_rate: StrictStr
    payer: str
    shares: dict[str, StrictStr]
    comment: str = ""
    revision_id: int | None = Field(None, gt=0)
    deposit: bool = False
    correction_of: int | None = Field(None, gt=0)


class SettlementRequest(CommandRequest):
    profit_revision_id: int | None = Field(None, gt=0)
    debtor: str
    creditor: str
    currency: str
    amount: StrictStr
    method: str = "direct"
    instrument: str | None = None
    quoted_rate: StrictStr | None = None
    client_id: int | None = Field(None, gt=0)
    obligation_ids: list[int] | None = None
    revision_id: int | None = Field(None, gt=0)


class ExpenseReversalRequest(CommandRequest):
    profit_revision_id: int | None = Field(None, gt=0)
    reason: str = Field(min_length=1)
    revision_id: int | None = Field(None, gt=0)


class CloseRequest(CommandRequest):
    profit_revision_id: int | None = Field(None, gt=0)
    period: date
    revision_id: int | None = Field(None, gt=0)


class RevisionRequest(CommandRequest):
    profit_revision_id: int | None = Field(None, gt=0)
    period: date
    reason: str = Field(min_length=1)


def register_expense_routes(app, container, actor):
    def common(payload, user):
        return {
            "key": payload.idempotency_key,
            "actor_id": user,
            "business_date": business_day(payload.effective_at, payload.business_date),
            "effective_at": payload.effective_at,
        }

    @app.get("/api/v1/expense-categories")
    async def categories(user: int = Depends(actor)):
        return response(await container.expense_queries.categories())

    @app.get("/api/v1/expense-participants")
    async def participants(user: int = Depends(actor)):
        return response(await container.expense_queries.participants())

    @app.get("/api/v1/expenses")
    async def expenses(
        period: date | None = None,
        participant_id: int | None = Query(None,gt=0),
        currency: str | None = None,
        before_id: int | None = Query(None, gt=0),
        limit: int = Query(50, ge=1, le=100),
        user: int = Depends(actor),
    ):
        items = await container.expense_queries.list(before_id, limit, month(period) if period else None, participant_id, currency)
        return response(
            {"items": items, "next_before_id": items[-1]["id"] if len(items) == limit else None}
        )

    @app.post("/api/v1/expenses")
    async def record(payload: ExpenseRequest, user: int = Depends(actor)):
        return operation_response(
            await container.expenses.record(
                **common(payload, user),
                **payload.model_dump(exclude={"idempotency_key", "effective_at", "business_date"}),
            )
        )

    @app.get("/api/v1/expenses/{expense_id}")
    async def detail(expense_id: int, user: int = Depends(actor)):
        return response(await container.expense_queries.detail(expense_id))

    @app.get("/api/v1/expense-debts")
    async def debts(
        participant_id: int | None = Query(None, gt=0),
        currency: str | None = None,
        user: int = Depends(actor),
    ):
        return response(await container.expense_queries.debts(participant_id, currency))

    @app.post("/api/v1/expense-settlements")
    async def settle(payload: SettlementRequest, user: int = Depends(actor)):
        return operation_response(
            await container.expenses.settle(
                **common(payload, user),
                **payload.model_dump(exclude={"idempotency_key", "effective_at", "business_date"}),
            )
        )

    @app.get("/api/v1/expense-settlements/{resolution_id}")
    async def resolution(resolution_id: int, user: int = Depends(actor)):
        return response(await container.expense_queries.resolution(resolution_id))

    @app.post("/api/v1/expense-operations/{operation_id}/reversal")
    async def reverse(
        operation_id: int, payload: ExpenseReversalRequest, user: int = Depends(actor)
    ):
        return operation_response(
            await container.expenses.reverse(
                **common(payload, user),
                operation_id=operation_id,
                reason=payload.reason,
                revision_id=payload.revision_id,
                profit_revision_id=payload.profit_revision_id,
            )
        )

    @app.get("/api/v1/expense-statements")
    async def statement(
        period: date, revision: int | None = Query(None, gt=0), user: int = Depends(actor)
    ):
        return response(await container.expense_queries.statement(month(period), revision))

    @app.post("/api/v1/expense-periods/close")
    async def close(payload: CloseRequest, user: int = Depends(actor)):
        business_day(payload.effective_at, payload.business_date)
        return operation_response(
            await container.expenses.close(
                key=payload.idempotency_key,
                actor_id=user,
                period=payload.period,
                effective_at=payload.effective_at,
                revision_id=payload.revision_id,
                profit_revision_id=payload.profit_revision_id,
            )
        )

    @app.post("/api/v1/expense-periods/revisions")
    async def revise(payload: RevisionRequest, user: int = Depends(actor)):
        return operation_response(
            await container.expenses.start_revision(
                **common(payload, user), period=payload.period, reason=payload.reason, profit_revision_id=payload.profit_revision_id
            )
        )

    @app.get("/api/v1/expense-periods/revisions")
    async def revisions(period: date, user: int = Depends(actor)):
        return response(await container.expense_queries.revisions(month(period)))
