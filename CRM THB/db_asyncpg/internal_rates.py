"""Read-only exchange rates calculated from active, completed exchange legs."""
import json

from domain.finance.models import FxQuote


class InternalRates:
    def __init__(self, database):
        self._db = database

    async def rows(self, at=None):
        return [dict(row) for row in await self._db.fetch("""
            WITH observations AS (
                SELECT l.instrument_code,l.quantity,
                    CASE WHEN other.instrument_code='USDT' THEN other.quantity
                         ELSE l.quantity*r.usdt_per_unit END AS usdt_value,
                    o.effective_at
                FROM deals d JOIN active_financial_originals o ON o.id=d.operation_id
                JOIN deal_legs l ON l.deal_id=d.id
                JOIN deal_legs other ON other.deal_id=d.id AND other.number<>l.number
                JOIN fx_rate_snapshots r ON r.id=l.rate_snapshot_id
                WHERE d.kind IN ('fx_trade','cash_exchange','best_change') AND d.status='posted'
                    AND o.effective_at<=coalesce($1::timestamptz,clock_timestamp())
            ), totals AS (
                SELECT instrument_code,sum(quantity) AS quantity,sum(usdt_value) AS usdt_value,
                    max(effective_at) AS effective_at FROM observations GROUP BY instrument_code
            )
            SELECT i.code AS instrument,i.currency_code,
                CASE WHEN i.code='USDT' THEN 1::numeric
                     WHEN t.usdt_value>0 THEN round(CASE WHEN i.currency_code='THB'
                         THEN t.quantity/t.usdt_value ELSE t.usdt_value/t.quantity END,18) END AS quoted_rate,
                CASE WHEN i.currency_code='THB' THEN 'units_per_usdt' ELSE 'usdt_per_unit' END AS quoted_direction,
                CASE WHEN i.code='USDT' THEN 1::numeric
                     WHEN t.usdt_value>0 THEN round(t.usdt_value/t.quantity,18) END AS usdt_per_unit,
                t.effective_at
            FROM instruments i LEFT JOIN totals t ON t.instrument_code=i.code
            WHERE i.is_active ORDER BY i.code
        """, at)]

    async def quote(self, instrument, at=None, key=None):
        # The original operation owns its valuation, even if the average changed on retry.
        if key is not None:
            saved = await self._db.fetchval(
                "SELECT command_details FROM financial_operations WHERE idempotency_key=$1 AND status='posted'",key)
            if saved is not None:
                details=json.loads(saved) if isinstance(saved,str) else saved
                field='quoted_rate' if 'quoted_rate' in details else 'rate'
                if field in details:
                    rate=details[field]
                    if rate is None:
                        return None
                    currency=await self._db.fetchval("SELECT currency_code FROM instruments WHERE code=$1",instrument)
                    return FxQuote(currency,rate,'units_per_usdt' if currency=='THB' else 'usdt_per_unit',instrument)
        if instrument=='USDT':
            return FxQuote('USDT',1,instrument=instrument)
        row = next((r for r in await self.rows(at) if r['instrument']==instrument), None)
        if row is None or row['quoted_rate'] is None:
            return None
        return FxQuote(row['currency_code'],row['quoted_rate'],row['quoted_direction'],instrument)
