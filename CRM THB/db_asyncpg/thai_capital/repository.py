from decimal import Decimal

import asyncpg

from domain.capital import CapitalLot
from domain.finance.models import InvalidPosting, PermissionDenied


class CapitalRepository:
    def __init__(self, connection: asyncpg.Connection):
        self._db = connection

    async def permission(self, actor_id, permission):
        if not await self._db.fetchval("""
            SELECT EXISTS(SELECT 1 FROM users u JOIN user_roles r ON r.user_id=u.id
                JOIN role_permissions p ON p.role=r.role
                WHERE u.id=$1 AND u.is_active AND p.permission=$2)
        """, actor_id, permission):
            raise PermissionDenied("Недостаточно прав для оборотки")

    async def lock_agreement(self, agreement_id, day=None):
        row = await self._db.fetchrow("SELECT * FROM capital_agreements WHERE id=$1 FOR UPDATE", agreement_id)
        if row is None or not row['is_active'] or (day is not None and (
                day < row['opened_on'] or (row['closed_on'] is not None and day > row['closed_on']))):
            raise InvalidPosting("Договор не найден или не действует на выбранную дату")
        return dict(row)

    async def create_agreement(self, operation_id, actor_id, day, percent, name, party_id):
        if (name is None) == (party_id is None):
            raise InvalidPosting("Укажите нового инвестора или существующего участника")
        if name is not None:
            if not name.strip():
                raise InvalidPosting("Имя инвестора обязательно")
            party_id = await self._db.fetchval("INSERT INTO parties(name,kind) VALUES ($1,'person') RETURNING id", name.strip())
        if not await self._db.fetchval("SELECT EXISTS(SELECT 1 FROM parties WHERE id=$1 AND is_active)", party_id):
            raise InvalidPosting("Инвестор не найден")
        owner = await self._db.fetchval("SELECT EXISTS(SELECT 1 FROM party_roles WHERE party_id=$1 AND role='owner')", party_id)
        if owner and percent != 0:
            raise InvalidPosting("Собственники получают долю прибыли, процент по капиталу равен 0")
        await self._db.execute("INSERT INTO party_roles VALUES ($1,'investor') ON CONFLICT DO NOTHING", party_id)
        agreement = await self._db.fetchval("""
            INSERT INTO capital_agreements(investor_party_id,principal_kind,opened_on,created_operation_id)
            VALUES ($1,$2,$3,$4) RETURNING id
        """, party_id, 'owner_equity' if owner else 'investor_liability', day, operation_id)
        await self._db.execute("""
            INSERT INTO capital_interest_terms(agreement_id,monthly_interest_percent,valid_from,created_by,change_operation_id)
            VALUES ($1,$2,$3,$4,$5)
        """, agreement, percent, day, actor_id, operation_id)
        await self._db.execute("SELECT provision_capital_accounts($1)", agreement)

    async def change_term(self, operation_id, agreement_id, actor_id, valid_from, percent):
        agreement = await self.lock_agreement(agreement_id, valid_from)
        if agreement['principal_kind'] == 'owner_equity' and percent != 0:
            raise InvalidPosting("Процент собственников должен оставаться 0")
        latest = await self._db.fetchrow("SELECT * FROM capital_interest_terms WHERE agreement_id=$1 ORDER BY valid_from DESC LIMIT 1", agreement_id)
        if latest is None or valid_from <= latest['valid_from']:
            raise InvalidPosting("Новая ставка должна начинаться после последнего условия")
        if await self._db.fetchval("""
            SELECT EXISTS(SELECT 1 FROM investor_interest_balances WHERE agreement_id=$1
                AND period+interval '1 month'>$2::date)
        """, agreement_id, valid_from):
            raise InvalidPosting("Ставка затрагивает уже начисленный месяц")
        await self._db.execute("UPDATE capital_interest_terms SET valid_until=$2 WHERE id=$1", latest['id'], valid_from)
        await self._db.execute("""
            INSERT INTO capital_interest_terms(agreement_id,monthly_interest_percent,valid_from,created_by,change_operation_id)
            VALUES ($1,$2,$3,$4,$5)
        """, agreement_id, percent, valid_from, actor_id, operation_id)

    async def capital_day(self, agreement_id, day, effective_at):
        last = await self._db.fetchrow("""
            SELECT o.business_date,o.effective_at FROM active_financial_originals o
            WHERE o.id IN (SELECT operation_id FROM capital_contributions WHERE agreement_id=$1
                UNION ALL SELECT operation_id FROM capital_withdrawals WHERE agreement_id=$1)
            ORDER BY o.business_date DESC,o.effective_at DESC,o.id DESC LIMIT 1
        """, agreement_id)
        if last is not None and (day, effective_at) < (last['business_date'], last['effective_at']):
            raise InvalidPosting("Движения капитала проводятся в хронологическом порядке")
        if await self._db.fetchval("""
            SELECT EXISTS(SELECT 1 FROM investor_interest_balances WHERE agreement_id=$1
                AND period+interval '1 month'>$2::date)
        """, agreement_id, day):
            raise InvalidPosting("Сначала отмените начисление, которое затрагивает изменение капитала")

    async def lots(self, agreement_id, instrument, day):
        # Agreement lock serializes availability reads; lock lots in deterministic FIFO order as well.
        rows = await self._db.fetch("""
            SELECT b.* FROM capital_lot_balances b JOIN capital_contributions c ON c.id=b.id
            WHERE b.agreement_id=$1 AND b.business_date<=$3 AND b.remaining_quantity>0
                AND (b.instrument_code=$2 OR (b.instrument_code IN ('THB_CASH','THB_BANK') AND $2 IN ('THB_CASH','THB_BANK')))
            ORDER BY b.business_date,b.effective_at,b.operation_id,b.id FOR UPDATE OF c
        """, agreement_id, instrument, day)
        return tuple(CapitalLot(r['id'], r['operation_id'], r['remaining_quantity'], r['remaining_principal']) for r in rows)

    async def _rate_id(self, operation_id):
        return await self._db.fetchval("SELECT rate_snapshot_id FROM journal_lines WHERE operation_id=$1 AND rate_snapshot_id IS NOT NULL ORDER BY line_number LIMIT 1", operation_id)

    async def contribution(self, operation_id, agreement_id, instrument, quantity, principal, comment):
        await self._db.execute("""
            INSERT INTO capital_contributions(agreement_id,operation_id,instrument_code,quantity,rate_snapshot_id,principal_usdt)
            VALUES ($1,$2,$3,$4,$5,$6)
        """, agreement_id, operation_id, instrument, quantity, await self._rate_id(operation_id), principal)
        if comment.strip():
            await self._db.execute("INSERT INTO operation_audit_notes VALUES ($1,$2)", operation_id, comment)

    async def withdrawal(self, operation_id, agreement_id, instrument, quantity, allocations, comment):
        principal = sum((a.principal for a in allocations), Decimal(0))
        withdrawal = await self._db.fetchval("""
            INSERT INTO capital_withdrawals(agreement_id,operation_id,payout_instrument_code,payout_quantity,principal_usdt)
            VALUES ($1,$2,$3,$4,$5) RETURNING id
        """, agreement_id, operation_id, instrument, quantity, principal)
        for a in allocations:
            await self._db.execute("INSERT INTO capital_withdrawal_allocations VALUES ($1,$2,$3,$4,$5)",
                withdrawal, a.lot_id, agreement_id, a.quantity, a.principal)
        if comment.strip():
            await self._db.execute("INSERT INTO operation_audit_notes VALUES ($1,$2)", operation_id, comment)

    async def interest_basis(self, agreement_id, period):
        if await self._db.fetchval("SELECT EXISTS(SELECT 1 FROM investor_interest_balances WHERE agreement_id=$1 AND period=$2)", agreement_id, period):
            raise InvalidPosting("Проценты за этот месяц уже начислены")
        term = await self._db.fetchrow("""
            SELECT * FROM capital_interest_terms WHERE agreement_id=$1 AND valid_from<=$2
                AND (valid_until IS NULL OR valid_until>$2) ORDER BY valid_from DESC LIMIT 1
        """, agreement_id, period)
        if term is None:
            term = await self._db.fetchrow("""
                SELECT * FROM capital_interest_terms WHERE agreement_id=$1
                    AND valid_from<$2::date+interval '1 month' ORDER BY valid_from LIMIT 1
            """, agreement_id, period)
        if term is None:
            raise InvalidPosting("Нет условий для выбранного месяца")
        basis = await self._db.fetchval("""
            SELECT coalesce(sum(amount),0) FROM (
                SELECT c.principal_usdt AS amount FROM capital_contributions c
                    JOIN active_financial_originals o ON o.id=c.operation_id WHERE c.agreement_id=$1 AND o.business_date<$2
                UNION ALL SELECT -w.principal_usdt FROM capital_withdrawals w
                    JOIN active_financial_originals o ON o.id=w.operation_id WHERE w.agreement_id=$1 AND o.business_date<$2
            ) movements
        """, agreement_id, period)
        changed = await self._db.fetchval("""
            SELECT EXISTS(SELECT 1 FROM active_financial_originals o
                WHERE o.business_date>=$2 AND o.business_date<$2::date+interval '1 month'
                AND o.id IN (SELECT operation_id FROM capital_contributions WHERE agreement_id=$1
                    UNION ALL SELECT operation_id FROM capital_withdrawals WHERE agreement_id=$1))
                OR EXISTS(SELECT 1 FROM capital_interest_terms WHERE agreement_id=$1
                    AND valid_from>=$2 AND valid_from<$2::date+interval '1 month')
        """, agreement_id, period)
        return {'amount': basis, 'term_id': term['id'], 'percent': term['monthly_interest_percent'], 'changed': changed}

    async def accrue(self, operation_id, agreement_id, period, basis, calculated, approved, reason):
        await self._db.execute("""
            INSERT INTO investor_interest_accruals(agreement_id,term_id,period,basis_as_of,basis_usdt,
                calculated_usdt,adjustment_usdt,adjustment_reason,operation_id,monthly_interest_percent,decision_reason)
            VALUES ($1,$2,$3,$3,$4,$5,$6,$7,$8,$9,$7)
        """, agreement_id, basis['term_id'], period, basis['amount'], calculated, approved-calculated,
            reason, operation_id, basis['percent'])
        await self._db.execute("INSERT INTO capital_decisions VALUES ($1,$2,$3)", operation_id, agreement_id, reason)

    async def source_operations(self, agreement_id, before):
        return await self._db.fetch("""
            SELECT o.id,o.kind,o.command_details->>'instrument' AS instrument
            FROM active_financial_originals o WHERE o.business_date<$2
            AND o.id IN (SELECT operation_id FROM capital_contributions WHERE agreement_id=$1
                UNION ALL SELECT operation_id FROM capital_withdrawals WHERE agreement_id=$1)
            ORDER BY o.id
        """, agreement_id, before)

    async def unpaid(self, agreement_id, day):
        rows = await self._db.fetch("""
            SELECT * FROM investor_interest_balances WHERE agreement_id=$1 AND business_date<=$2
                AND remaining_usdt>0 ORDER BY period,operation_id,id
        """, agreement_id, day)
        return [dict(r) for r in rows]

    async def pay(self, operation_id, agreement, instrument, quantity, discharged, allocations, reason,
                  *, quote, actor_id, effective_at):
        rate_id = await self._db.fetchval("""
            INSERT INTO fx_rate_snapshots(currency_code,instrument_code,quoted_direction,quoted_rate,
                usdt_per_unit,effective_at,created_by) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
        """, quote.currency, quote.instrument, quote.direction, quote.quoted_rate, quote.usdt_per_unit,
            effective_at, actor_id)
        payment = await self._db.fetchval("""
            INSERT INTO distribution_payments(recipient_party_id,operation_id,instrument_code,quantity,discharged_usdt,rate_snapshot_id)
            VALUES ($1,$2,$3,$4,$5,$6) RETURNING id
        """, agreement['investor_party_id'], operation_id, instrument, quantity, discharged, rate_id)
        for accrual_id, _source_id, amount in allocations:
            await self._db.execute("INSERT INTO investor_interest_payment_allocations VALUES ($1,$2,$3)", payment, accrual_id, amount)
        await self._db.execute("INSERT INTO capital_decisions VALUES ($1,$2,$3)", operation_id, agreement['id'], reason)

    async def reversal_target(self, operation_id, agreement_id):
        target = await self._db.fetchval("""
            SELECT agreement_id FROM capital_contributions WHERE operation_id=$1
            UNION ALL SELECT agreement_id FROM capital_withdrawals WHERE operation_id=$1
            UNION ALL SELECT agreement_id FROM investor_interest_accruals WHERE operation_id=$1
            UNION ALL SELECT agreement_id FROM capital_decisions d JOIN financial_operations o ON o.id=d.operation_id
                WHERE d.operation_id=$1 AND o.kind='interest_payment'
        """, operation_id)
        if target != agreement_id:
            raise InvalidPosting("Операция не относится к этому договору оборотки")
