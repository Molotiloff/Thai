from calendar import monthrange
from datetime import date, datetime

from fastapi import Depends, Query
from pydantic import BaseModel, Field, StrictStr

from api.thai_common import business_day, operation_response, response


class CommandRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    effective_at: datetime
    business_date: date | None = None


class AgreementRequest(CommandRequest):
    name: str | None = Field(None, min_length=1, max_length=300)
    party_id: int | None = Field(None, gt=0)
    monthly_interest_percent: StrictStr = '0'


class TermRequest(CommandRequest):
    valid_from: date
    monthly_interest_percent: StrictStr
    reason: str = Field(min_length=1)


class ContributionRequest(CommandRequest):
    instrument: str
    quantity: StrictStr
    quoted_rate: StrictStr | None = None
    comment: str = ''


class WithdrawalRequest(CommandRequest):
    instrument: str
    quantity: StrictStr
    comment: str = ''


class AccrualRequest(CommandRequest):
    profit_revision_id: int | None = Field(None, gt=0)
    period: date
    approved_usdt: StrictStr | None = None
    reason: str = Field(min_length=1)


class PaymentRequest(CommandRequest):
    instrument: str = 'USDT'
    quantity: StrictStr
    quoted_rate: StrictStr | None = None
    reason: str = Field(min_length=1)


class ReversalRequest(CommandRequest):
    profit_revision_id: int | None = Field(None, gt=0)
    reason: str = Field(min_length=1)


def register_capital_routes(app, container, actor):
    prefix = '/api/v1/capital'

    def common(payload, user):
        return {'key': payload.idempotency_key, 'actor_id': user, 'effective_at': payload.effective_at,
                'business_date': business_day(payload.effective_at, payload.business_date)}

    @app.get(prefix+'/investors')
    async def investors(user: int = Depends(actor)):
        return response(await container.capital_queries.investors())

    @app.get(prefix+'/agreements')
    async def agreements(investor_party_id: int | None = Query(None, gt=0), user: int = Depends(actor)):
        return response(await container.capital_queries.agreements(investor_party_id))

    @app.get(prefix+'/investors/{investor_id}/history')
    async def investor_history(investor_id: int, before_id: int | None = Query(None, gt=0),
                               limit: int = Query(50, ge=1, le=100), user: int = Depends(actor)):
        items = await container.capital_queries.history(None, before_id=before_id, limit=limit, investor_id=investor_id)
        return response({'items': items, 'next_before_id': items[-1]['id'] if len(items) == limit else None})

    @app.post(prefix+'/agreements')
    async def create_agreement(payload: AgreementRequest, user: int = Depends(actor)):
        result = await container.capital.create_agreement(**common(payload, user), name=payload.name,
            party_id=payload.party_id, monthly_interest_percent=payload.monthly_interest_percent)
        return operation_response(result)

    @app.get(prefix+'/agreements/{agreement_id}')
    async def detail(agreement_id: int, user: int = Depends(actor)):
        return response(await container.capital_queries.detail(agreement_id))

    @app.get(prefix+'/agreements/{agreement_id}/history')
    async def history(agreement_id: int, before_id: int | None = Query(None, gt=0),
                      limit: int = Query(50, ge=1, le=100), user: int = Depends(actor)):
        items = await container.capital_queries.history(agreement_id, before_id=before_id, limit=limit)
        return response({'items': items, 'next_before_id': items[-1]['id'] if len(items) == limit else None})

    @app.post(prefix+'/agreements/{agreement_id}/terms')
    async def terms(agreement_id: int, payload: TermRequest, user: int = Depends(actor)):
        business_day(payload.effective_at, payload.business_date)
        return operation_response(await container.capital.change_term(key=payload.idempotency_key,
            actor_id=user, agreement_id=agreement_id, valid_from=payload.valid_from,
            effective_at=payload.effective_at, monthly_interest_percent=payload.monthly_interest_percent, reason=payload.reason))

    @app.post(prefix+'/agreements/{agreement_id}/contributions')
    async def contribute(agreement_id: int, payload: ContributionRequest, user: int = Depends(actor)):
        return operation_response(await container.capital.contribute(**common(payload, user), agreement_id=agreement_id,
            instrument=payload.instrument, quantity=payload.quantity, quoted_rate=payload.quoted_rate, comment=payload.comment))

    @app.post(prefix+'/agreements/{agreement_id}/withdrawals')
    async def withdraw(agreement_id: int, payload: WithdrawalRequest, user: int = Depends(actor)):
        return operation_response(await container.capital.withdraw(**common(payload, user), agreement_id=agreement_id,
            instrument=payload.instrument, quantity=payload.quantity, comment=payload.comment))

    @app.post(prefix+'/agreements/{agreement_id}/accruals')
    async def accrue(agreement_id: int, payload: AccrualRequest, user: int = Depends(actor)):
        args = common(payload, user)
        args['business_date'] = payload.business_date or date(payload.period.year, payload.period.month,
            monthrange(payload.period.year, payload.period.month)[1])
        return operation_response(await container.capital.accrue(**args, agreement_id=agreement_id,
            period=payload.period, approved_usdt=payload.approved_usdt, reason=payload.reason, profit_revision_id=payload.profit_revision_id))

    @app.post(prefix+'/agreements/{agreement_id}/interest-payments')
    async def pay(agreement_id: int, payload: PaymentRequest, user: int = Depends(actor)):
        return operation_response(await container.capital.pay_interest(**common(payload, user), agreement_id=agreement_id,
            instrument=payload.instrument, quantity=payload.quantity, quoted_rate=payload.quoted_rate, reason=payload.reason))

    @app.post(prefix+'/agreements/{agreement_id}/operations/{operation_id}/reversal')
    async def reverse(agreement_id: int, operation_id: int, payload: ReversalRequest, user: int = Depends(actor)):
        return operation_response(await container.capital.reverse(**common(payload, user), agreement_id=agreement_id,
            original_id=operation_id, reason=payload.reason, profit_revision_id=payload.profit_revision_id))
