import json

from domain.finance.models import InvalidPosting, canonical_json


class ManualProfitRepository:
    def __init__(self, connection):
        self._db = connection

    async def accounts(self):
        return {row['purpose']: row['id'] for row in await self._db.fetch("""
            SELECT id,purpose FROM ledger_accounts WHERE purpose IN
                ('manual_profit_income','manual_profit_loss','historical_profit_offset') AND is_active
        """)}

    async def create(self, actor_id, client_id, attributes, reference):
        return await self._db.fetchval("""
            INSERT INTO deals(client_id,kind,source,status,external_reference,created_by,attributes)
            VALUES ($1,'manual_profit','thai','draft',$2,$3,$4::jsonb) RETURNING id
        """, client_id, reference, actor_id, canonical_json(attributes))

    async def post(self, deal_id, operation_id):
        await self._db.execute("UPDATE deals SET status='posted',operation_id=$2 WHERE id=$1", deal_id, operation_id)

    async def current(self, deal_id):
        root = await self._db.fetchval("""
            WITH RECURSIVE parents AS (
                SELECT id,0 AS depth FROM deals WHERE id=$1 AND kind='manual_profit'
                UNION ALL SELECT r.deal_id,p.depth+1 FROM parents p
                    JOIN deal_reversals r ON r.replacement_deal_id=p.id
            ) SELECT id FROM parents ORDER BY depth DESC LIMIT 1
        """, deal_id)
        if root is None:
            raise InvalidPosting('Запись прибыли не найдена')
        await self._db.fetchval('SELECT id FROM deals WHERE id=$1 FOR UPDATE', root)
        row = await self._db.fetchrow("""
            WITH RECURSIVE chain AS (
                SELECT id,0 AS depth FROM deals WHERE id=$1
                UNION ALL SELECT r.replacement_deal_id,c.depth+1 FROM chain c
                    JOIN deal_reversals r ON r.deal_id=c.id WHERE r.replacement_deal_id IS NOT NULL
            ) SELECT d.*,o.business_date,o.effective_at,
                EXISTS(SELECT 1 FROM deal_reversals WHERE deal_id=d.id) AS cancelled
                FROM deals d JOIN financial_operations o ON o.id=d.operation_id
                WHERE d.id=(SELECT id FROM chain ORDER BY depth DESC LIMIT 1)
                FOR UPDATE OF d
        """, root)
        if row is None:
            raise InvalidPosting('Запись прибыли ещё не проведена')
        return {**dict(row), 'root_id': root, 'attributes': json.loads(row['attributes'])}

    async def reversed(self, deal_id, operation_id, reason, replacement_id=None):
        await self._db.execute('INSERT INTO deal_reversals VALUES ($1,$2,$3,$4)',
                               deal_id, operation_id, reason, replacement_id)
