from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.auth import AuthError, decode_access_token
from api.thai_common import business_day, operation_response, response
from app.container import ApplicationContainer
from config import Config
from db_asyncpg.migrator import AlembicMigrator
from db_asyncpg.schema_identity import is_thai_runtime_ready, thai_runtime_status
from domain.finance.models import FinanceError, decimal, revision_details


class CashTransferRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    source_account_id: int = Field(gt=0)
    target_account_id: int = Field(gt=0)
    quantity: StrictStr
    effective_at: datetime
    business_date: date | None = None


class ReversalRequest(BaseModel):
    profit_revision_id: int | None = Field(None, gt=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1)
    effective_at: datetime
    business_date: date | None = None


def create_thai_finance_api(*, container: ApplicationContainer, jwt_secret: str,
                            cors_origins: list[str] | None = None,
                            oidc_config: Config | None = None) -> FastAPI:
    if len(jwt_secret) < 32:
        raise ValueError("Thai API JWT secret must contain at least 32 characters")
    expected_revision=AlembicMigrator('',schema='thai').head_revision()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with container.pool.acquire() as connection:
            status=await thai_runtime_status(connection)
        if not is_thai_runtime_ready(status,expected_revision):
            raise RuntimeError("Financial API requires the current isolated Thai schema")
        await container.initialize_business_chats()
        yield

    app = FastAPI(title="Thai CRM financial API", version="0.9.0", lifespan=lifespan)
    app.state.container = container

    @app.get('/health/live',include_in_schema=False)
    async def live():
        return {'status':'ok'}

    @app.get('/health/ready',include_in_schema=False)
    async def ready():
        import asyncpg
        try:
            async with container.pool.acquire(timeout=2) as connection:
                status=await thai_runtime_status(connection)
            available=is_thai_runtime_ready(status,expected_revision)
        except (asyncpg.PostgresError,asyncpg.InterfaceError,OSError,TimeoutError):
            available=False
        return JSONResponse(status_code=200 if available else 503,
            content={'status':'ready' if available else 'not_ready'},headers={'Cache-Control':'no-store'})
    from api.thai_auth import register_auth_routes

    register_auth_routes(app, container, jwt_secret, oidc_config)
    if cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=cors_origins,
                           allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type"])
    bearer = HTTPBearer(auto_error=False)

    async def actor(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> int:
        if credentials is None:
            raise HTTPException(401, detail="Not authenticated")
        try:
            subject = int(decode_access_token(credentials.credentials, secret=jwt_secret).sub)
        except (AuthError, ValueError, TypeError, KeyError):
            raise HTTPException(401, detail="Not authenticated") from None
        actor_id = await container.queries.actor_by_telegram_id(subject)
        if actor_id is None:
            raise HTTPException(401, detail="Not authenticated")
        if not await container.queries.can_read(actor_id):
            raise HTTPException(403, detail="Insufficient permissions")
        return actor_id

    @app.exception_handler(FinanceError)
    async def finance_error(request: Request, error: FinanceError):
        code = 403 if error.code == "permission_denied" else (
            422 if error.code == "invalid_posting" else 409)
        return JSONResponse(status_code=code, content={"error": {"code": error.code, "message": str(error)}})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException):
        codes = {401: "not_authenticated", 403: "permission_denied", 404: "not_found", 422: "invalid_request"}
        return JSONResponse(status_code=error.status_code,
            content={"error": {"code": codes.get(error.status_code, "http_error"), "message": str(error.detail)}},
            headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return JSONResponse(status_code=422,
            content={"error": {"code": "invalid_request", "message": "Некорректные параметры запроса",
                "fields": [{"path": list(item["loc"]), "type": item["type"]} for item in error.errors()]}})

    @app.get("/api/v1/finance/cash")
    async def cash(_: int = Depends(actor)):
        return response(await container.queries.cash_balances())

    @app.get("/api/v1/finance/operations")
    async def history(before_id: int | None = Query(None, gt=0), limit: int = Query(50, ge=1, le=100),
                      _: int = Depends(actor)):
        items = await container.queries.history(before_id=before_id, limit=limit)
        return response({"items": items, "next_before_id": items[-1]["id"] if len(items) == limit else None})

    @app.get("/api/v1/finance/operations/{operation_id}")
    async def detail(operation_id: int, _: int = Depends(actor)):
        return response(await container.queries.detail(operation_id))

    @app.post("/api/v1/finance/cash-transfers")
    async def transfer(payload: CashTransferRequest, user: int = Depends(actor)):
        result = await container.cash_transfers.transfer(key=payload.idempotency_key, actor_id=user,
            business_date=business_day(payload.effective_at, payload.business_date), effective_at=payload.effective_at,
            source_id=payload.source_account_id, target_id=payload.target_account_id, quantity=decimal(payload.quantity))
        return operation_response(result)

    @app.post("/api/v1/finance/operations/{operation_id}/reversal")
    async def reverse(operation_id: int, payload: ReversalRequest, user: int = Depends(actor)):
        request_id = await container.pool.fetchval("SELECT id FROM deals WHERE operation_id=$1 AND kind IN ('deposit','withdrawal','cash_exchange','fx_trade')",operation_id)
        if request_id is not None:
            return operation_response(await container.trading.reverse(deal_id=request_id,key=payload.idempotency_key,
                actor_id=user,business_date=business_day(payload.effective_at,payload.business_date),effective_at=payload.effective_at,
                reason=payload.reason,profit_revision_id=payload.profit_revision_id))
        result = await container.reversals.reverse(original_id=operation_id, key=payload.idempotency_key,
            actor_id=user, business_date=business_day(payload.effective_at, payload.business_date),
            effective_at=payload.effective_at, reason=payload.reason, details=revision_details({}, payload.profit_revision_id))
        return operation_response(result)

    @app.post("/api/v1/finance/projections/rebuild")
    async def rebuild(user: int = Depends(actor)):
        await container.projections.rebuild(user)
        return {"status": "rebuilt"}

    from api.thai_capital import register_capital_routes
    register_capital_routes(app, container, actor)
    from api.thai_clients import register_client_routes
    register_client_routes(app, container, actor)
    from api.thai_trading import register_trading_routes
    register_trading_routes(app,container,actor)
    from api.thai_expenses import register_expense_routes
    register_expense_routes(app,container,actor)
    from api.thai_profit import register_profit_routes
    register_profit_routes(app,container,actor)
    from api.thai_reports import register_report_routes
    register_report_routes(app,container,actor)
    return app
