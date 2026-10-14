from datetime import date

from fastapi import Depends, Query

from api.thai_common import response
from db_asyncpg.thai_reports import ReportQueries
from services.expenses.service import month


def register_report_routes(app, container, actor):
    queries = ReportQueries(container.pool)

    @app.get('/api/v1/session')
    async def session(user: int = Depends(actor)):
        return response(await queries.session(user))

    @app.get('/api/v1/instruments')
    async def instruments(user: int = Depends(actor)):
        return response(await queries.instruments())

    @app.get('/api/v1/clients/{client_id}')
    async def client(client_id: int, user: int = Depends(actor)):
        return response(await queries.client(client_id))

    @app.get('/api/v1/reports/dashboard')
    async def dashboard(period: date, user: int = Depends(actor)):
        return response(await queries.dashboard(month(period)))

    @app.get('/api/v1/reports/operations')
    async def operations(period: date | None = None, currency: str | None = None,
                         party_id: int | None = Query(None, gt=0), account_id: int | None = Query(None, gt=0),
                         before_id: int | None = Query(None, gt=0), limit: int = Query(50, ge=1, le=100),
                         user: int = Depends(actor)):
        items = await queries.operations(period=month(period) if period else None,currency=currency,
            party_id=party_id,account_id=account_id,before_id=before_id,limit=limit)
        return response({'items':items,'next_before_id':items[-1]['id'] if len(items)==limit else None})
