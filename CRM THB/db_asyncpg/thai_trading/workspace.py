"""One snapshot for the SkyEX deal board and its paginated activity register."""

import json
import re
from datetime import datetime

from domain.deal_policy import BASE_INSTRUMENTS
from domain.finance.models import InvalidPosting

BASE_SQL = "(" + ",".join(f"'{code}'" for code in BASE_INSTRUMENTS) + ")"

ENTRIES = f"""
WITH entries AS (
 SELECT 'deal:'||d.id AS entry_id,d.id AS deal_id,d.operation_id,d.client_id,
     coalesce(p.name,'Кассовый обмен') AS client_name,u.display_name AS created_by,
     CASE WHEN d.kind IN ('deposit','withdrawal','cash_exchange') AND d.status='posted'
        THEN coalesce(posted_op.effective_at,d.created_at) ELSE d.created_at END AS created_at,
     CASE WHEN d.kind IN ('deposit','withdrawal') AND d.status='posted'
      AND NOT EXISTS(SELECT 1 FROM active_financial_originals a WHERE a.id=d.operation_id)
      THEN 'cancelled' ELSE d.effective_status END AS status,
     CASE WHEN d.kind IN ('best_change','deposit','withdrawal') THEN d.kind
          WHEN d.attributes->'receive'->>'instrument' IN {BASE_SQL}
           AND d.attributes->'pay'->>'instrument' NOT IN {BASE_SQL} THEN 'sale'
          WHEN d.attributes->'pay'->>'instrument' IN {BASE_SQL}
           AND d.attributes->'receive'->>'instrument' NOT IN {BASE_SQL} THEN 'purchase'
          ELSE 'conversion' END AS kind,
     d.attributes, '/deals/'||d.id AS href
 FROM effective_deals d JOIN users u ON u.id=d.created_by
 LEFT JOIN financial_operations posted_op ON posted_op.id=d.operation_id
 LEFT JOIN clients c ON c.id=d.client_id LEFT JOIN parties p ON p.id=c.party_id
 UNION ALL
 SELECT 'operation:'||o.id,NULL::bigint,o.id,c.id,coalesce(p.name,'Клиент'),u.display_name,
     o.recorded_at,CASE WHEN a.id IS NULL THEN 'cancelled' ELSE 'posted' END,
     CASE o.kind WHEN 'wallet_deposit' THEN 'deposit' WHEN 'wallet_withdrawal' THEN 'withdrawal'
          WHEN 'wallet_transfer' THEN 'client_transfer' ELSE 'thb_transfer' END,
     o.command_details || jsonb_build_object('target_name',tp.name),'/operations/'||o.id
 FROM financial_operations o JOIN users u ON u.id=o.created_by
 LEFT JOIN active_financial_originals a ON a.id=o.id
 LEFT JOIN clients c ON c.id=coalesce((o.command_details->>'client')::bigint,
                                     (o.command_details->>'source_client')::bigint)
 LEFT JOIN parties p ON p.id=c.party_id
 LEFT JOIN clients tc ON tc.id=(o.command_details->>'target_client')::bigint
 LEFT JOIN parties tp ON tp.id=tc.party_id
 WHERE o.status='posted' AND o.kind IN ('wallet_deposit','wallet_withdrawal','wallet_conversion','wallet_transfer')
 AND NOT EXISTS(SELECT 1 FROM deals cash_deal WHERE cash_deal.operation_id=o.id)
), selected AS (
 SELECT * FROM entries WHERE ($1::text='' OR client_name ILIKE '%'||$1||'%'
     OR attributes->>'target_name' ILIKE '%'||$1||'%'
     OR entry_id ILIKE '%'||$1||'%')
     AND ($2::text IS NULL OR status=$2) AND ($3::text IS NULL OR kind=$3)
)
"""


class DealWorkspaceQueries:
    def __init__(self, pool):
        self._pool = pool

    async def workspace(self, *, query="", status=None, kind=None, before=None, limit=50):
        if status and status not in ("draft", "ready", "posted", "cancelled"):
            raise InvalidPosting("Неизвестный статус сделки")
        if kind and kind not in (
            "sale",
            "purchase",
            "conversion",
            "best_change",
            "deposit",
            "withdrawal",
            "client_transfer",
            "thb_transfer",
        ):
            raise InvalidPosting("Неизвестный тип операции")
        stamp, identity = None, None
        if before:
            try:
                time, identity = before.split("|", 1)
                stamp = datetime.fromisoformat(time)
                if stamp.tzinfo is None or not re.fullmatch(r"(deal|operation):\d+", identity):
                    raise ValueError
            except ValueError:
                raise InvalidPosting("Некорректный курсор списка сделок") from None
        async with (
            self._pool.acquire() as db,
            db.transaction(isolation="repeatable_read", readonly=True),
        ):
            items = await db.fetch(
                ENTRIES
                + """
                SELECT * FROM selected WHERE ($4::timestamptz IS NULL OR (created_at,entry_id)<($4,$5))
                ORDER BY created_at DESC,entry_id DESC LIMIT $6
            """,
                query,
                status,
                kind,
                stamp,
                identity,
                limit,
            )
            summaries = await db.fetch(
                ENTRIES + "SELECT status,count(*) AS count FROM selected GROUP BY status",
                query,
                status,
                kind,
            )
            board = await db.fetch(
                ENTRIES
                + """
                SELECT * FROM (SELECT *,row_number() OVER(PARTITION BY status ORDER BY created_at DESC,entry_id DESC) AS rank
                    FROM selected) s WHERE rank<=12 ORDER BY created_at DESC,entry_id DESC
            """,
                query,
                status,
                kind,
            )

            def present(row):
                result = dict(row)
                result["attributes"] = json.loads(result["attributes"])
                return result

            return {
                "items": [present(r) for r in items],
                "board": [present(r) for r in board],
                "summaries": [dict(r) for r in summaries],
                "next_cursor": f"{items[-1]['created_at'].isoformat()}|{items[-1]['entry_id']}"
                if len(items) == limit
                else None,
            }
