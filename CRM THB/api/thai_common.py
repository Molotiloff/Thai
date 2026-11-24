from datetime import date, datetime
from decimal import Decimal
from typing import NotRequired
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from typing_extensions import TypedDict

from domain.finance.models import PostedOperation


class ValidationField(TypedDict):
    path: list[str | int]
    type: str


class ThaiError(TypedDict):
    code: str
    message: str
    fields: NotRequired[list[ValidationField]]


class ThaiErrorResponse(TypedDict):
    error: ThaiError


def business_day(effective_at: datetime, supplied: date | None) -> date:
    if effective_at.tzinfo is None or effective_at.utcoffset() is None:
        raise HTTPException(422, detail="effective_at must include a timezone")
    return supplied or effective_at.astimezone(ZoneInfo("Asia/Bangkok")).date()


def response(value) -> JSONResponse:
    return JSONResponse(jsonable_encoder(value, custom_encoder={Decimal: lambda v: format(v, "f")}))


def operation_response(operation: PostedOperation, **extra) -> JSONResponse:
    return response({"id": operation.id, "idempotency_key": operation.key,
                     "kind": operation.kind, "business_date": operation.business_date,
                     "reversal_of_id": operation.reversal_of, **extra})


