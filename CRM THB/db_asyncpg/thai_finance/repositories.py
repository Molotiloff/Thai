from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal, localcontext

import asyncpg

from domain.finance.models import (
    AccountState,
    InsufficientCash,
    InvalidPosting,
    InventoryPosition,
    JournalLine,
    LedgerContext,
    OperationCommand,
    PeriodClosed,
    PermissionDenied,
    PostedOperation,
    PostingMode,
    PostingPlan,
    PostingState,
    ReversalBlocked,
    Side,
    Valuation,
    canonical_json,
    exact,
)


def posted(row) -> PostedOperation:
    return PostedOperation(row["id"], row["idempotency_key"], row["command_hash"], row["kind"],
        LedgerContext(row["context"]), row["business_date"], PostingMode(row["posting_mode"]),
        row["reversal_of_id"])


class JournalRepository:
    def __init__(self, connection: asyncpg.Connection):
        self._connection = connection

    async def _permission(self, actor_id: int, permission: str) -> None:
        permitted = await self._connection.fetchval("""
            SELECT EXISTS(SELECT 1 FROM users u JOIN user_roles r ON r.user_id=u.id
                JOIN role_permissions p ON p.role=r.role
                WHERE u.id=$1 AND u.is_active AND p.permission=$2)
        """, actor_id, permission)
        if not permitted:
            raise PermissionDenied("Недостаточно прав для финансовой операции")

    async def guard(self, command: OperationCommand, permission: str) -> None:
        self._profit_revision_id = json.loads(command.details_json).get('profit_revision_id')
        self._actor_id = command.actor_id
        self._current_kind = command.kind
        if command.kind in ('best_change','profit_closure','profit_payment','platform_payment','profit_terms','profit_revision','profit_configure') or self._profit_revision_id:
            await self._connection.execute('SELECT pg_advisory_xact_lock(740810,7)')
        if command.kind=='profit_closure':
            await self._connection.execute('SELECT pg_advisory_xact_lock(740810,7)')
            await self._connection.execute('SELECT pg_advisory_xact_lock(740810,3)')
        if command.context == LedgerContext.EXPENSES:
            await self._connection.execute("SELECT pg_advisory_xact_lock(740810,7)")
        await self.lock_posting_period(profit_revision_id=self._profit_revision_id)
        await self._permission(command.actor_id, permission)
        await self._connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", command.key)
        # Replay is read-only and remains available after a month has been closed.
        if await self._connection.fetchval("SELECT id FROM financial_operations WHERE idempotency_key=$1", command.key):
            return
        if command.kind=='profit_revision' or self._profit_revision_id:
            await self._permission(command.actor_id,'period.revise')
        if self._profit_revision_id and not await self._profit_revision_available(command.business_date):
            raise InvalidPosting('Ревизия прибыли не открыта для месяца этой операции')
        closed = False if command.kind=='profit_revision' else await self._period_closed(command.context, command.business_date)
        if closed:
            raise PeriodClosed("Период закрыт; нужна отдельная ревизия")

    async def lock_posting_period(self, *, profit_revision_id=None):
        if profit_revision_id is not None:
            await self._connection.execute('SELECT pg_advisory_xact_lock(740810,7)')
        await self._connection.execute('SELECT pg_advisory_xact_lock_shared(740810,3)')
        # Before client/account locks: pricing pooled funds may touch several wallets.
        await self._connection.execute('SELECT pg_advisory_xact_lock(740810,22)')

    async def _profit_revision_available(self,day):
        if not getattr(self,'_profit_revision_id',None):
            return False
        return await self._connection.fetchval("""
            SELECT EXISTS(SELECT 1 FROM profit_period_revisions r JOIN active_financial_originals o ON o.id=r.operation_id
                WHERE r.id=$1 AND r.period=date_trunc('month',$2::date)::date
                    AND NOT EXISTS(SELECT 1 FROM profit_revision_completions c WHERE c.revision_id=r.id))
        """,self._profit_revision_id,day)

    async def _period_closed(self,context,day):
        locks = await self._connection.fetch("SELECT context FROM period_locks WHERE context IN ('ledger',$1) AND period=date_trunc('month',$2::date)::date",context.value,day)
        if locks and await self._profit_revision_available(day):
            return any(row['context'] not in ('ledger','profit') for row in locks)
        return bool(locks)

    async def guard_rebuild(self, actor_id: int) -> None:
        await self._connection.execute("SELECT pg_advisory_xact_lock(740810,3)")
        await self._permission(actor_id, "finance.rebuild")

    async def find_by_key(self, key: str) -> PostedOperation | None:
        row = await self._connection.fetchrow("SELECT * FROM financial_operations WHERE idempotency_key=$1", key)
        if row is None:
            return None
        if row["status"] != "posted":
            raise InvalidPosting("Ключ занят непроведённым документом")
        return posted(row)

    async def get(self, operation_id: int) -> PostedOperation:
        row = await self._connection.fetchrow("SELECT * FROM financial_operations WHERE id=$1 AND status='posted'", operation_id)
        if row is None:
            raise InvalidPosting("Проведённая операция не найдена")
        return posted(row)

    async def create(self, command: OperationCommand) -> int:
        return await self._connection.fetchval("""
            INSERT INTO financial_operations(kind,posting_mode,business_date,effective_at,created_by,
                idempotency_key,command_hash,context,reversal_of_id,reason,command_details)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb) RETURNING id
        """, command.kind, command.mode.value, command.business_date, command.effective_at,
            command.actor_id, command.key, command.fingerprint, command.context.value,
            command.reversal_of, command.reason, command.details_json)

    async def save_lines(self, operation_id: int, plan: PostingPlan, command: OperationCommand) -> None:
        for number, line in enumerate(plan.lines, 1):
            rate_id = line.rate_id
            if line.quote is not None:
                quote = line.quote
                rate_id = await self._connection.fetchval("""
                    INSERT INTO fx_rate_snapshots(currency_code,instrument_code,quoted_direction,
                        quoted_rate,usdt_per_unit,effective_at,created_by)
                    VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
                """, quote.currency, quote.instrument, quote.direction, quote.quoted_rate,
                    quote.usdt_per_unit, command.effective_at, command.actor_id)
            await self._connection.execute("""
                INSERT INTO journal_lines VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            """, operation_id, number, line.account_id, line.currency, line.side.value,
                line.native, line.usdt, line.valuation.value, rate_id,line.unvalued,line.valuation_component)

    async def add_dependency(self, source_id: int, dependent_id: int) -> None:
        if source_id >= dependent_id:
            raise InvalidPosting("Зависимость может ссылаться только на более раннюю операцию")
        source = await self._connection.fetchval(
            "SELECT id FROM financial_operations WHERE id=$1 AND status='posted' FOR UPDATE", source_id)
        if source is None or await self._connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM financial_operations WHERE reversal_of_id=$1 AND status='posted')", source_id):
            raise InvalidPosting("Зависимость требует проведённого исходного документа")
        await self._connection.execute("""
            INSERT INTO financial_operation_dependencies(source_operation_id,dependent_operation_id)
            VALUES ($1,$2)
        """, source_id, dependent_id)

    async def finish(self, operation_id: int) -> PostedOperation:
        await self._connection.execute("UPDATE financial_operations SET status='posted' WHERE id=$1", operation_id)
        return await self.get(operation_id)

    async def lines(self, operation_id: int) -> tuple[JournalLine, ...]:
        rows = await self._connection.fetch("SELECT * FROM journal_lines WHERE operation_id=$1 ORDER BY line_number", operation_id)
        return tuple(JournalLine(row["account_id"], row["currency_code"], Side(row["side"]),
            row["native_amount"], row["amount_usdt"], Valuation.HISTORICAL,
            rate_id=row["rate_snapshot_id"],unvalued=row['unvalued_native'],valuation_component=row['valuation_component']) for row in rows)

    async def check_reversible(self, operation: PostedOperation, *, allow_business: bool = False) -> None:
        await self._connection.fetchval("SELECT id FROM financial_operations WHERE id=$1 FOR UPDATE", operation.id)
        if operation.reversal_of is not None:
            raise ReversalBlocked("Сторно сторно требует отдельной команды")
        if operation.kind in {'capital_agreement', 'capital_terms', 'market_rate', 'expense_revision', 'expense_close', 'profit_terms', 'profit_revision', 'profit_configure'}:
            raise ReversalBlocked("Условия договора меняются новой версией, а не сторно")
        if await self._connection.fetchval("SELECT EXISTS(SELECT 1 FROM financial_operations WHERE reversal_of_id=$1)", operation.id):
            raise ReversalBlocked("Операция уже сторнирована")
        if await self._period_closed(operation.context, operation.business_date):
            raise PeriodClosed("Исходный период закрыт")
        if await self._connection.fetchval("""
            SELECT EXISTS(SELECT 1 FROM financial_operation_dependencies d
                JOIN financial_operations child ON child.id=d.dependent_operation_id
                WHERE d.source_operation_id=$1 AND child.status='posted'
                AND NOT EXISTS(SELECT 1 FROM financial_operations r WHERE r.reversal_of_id=child.id AND r.status='posted'))
        """, operation.id):
            raise ReversalBlocked("Есть неотменённые зависимые операции")
        if await self._connection.fetchval("""
            SELECT EXISTS(SELECT 1 FROM inventory_movements m JOIN financial_operations o ON o.id=m.operation_id
                WHERE m.instrument_code IN (SELECT instrument_code FROM inventory_movements WHERE operation_id=$1)
                AND o.id>$1 AND o.status='posted' AND o.reversal_of_id IS NULL
                AND NOT EXISTS(SELECT 1 FROM financial_operations r WHERE r.reversal_of_id=o.id AND r.status='posted'))
        """, operation.id):
            raise ReversalBlocked("После операции изменилась себестоимость кассы; сначала отмените последующие движения")
        if await self._connection.fetchval("""
            SELECT EXISTS(SELECT 1 FROM journal_lines l JOIN financial_operations o ON o.id=l.operation_id
                WHERE l.account_id IN (SELECT source.account_id FROM journal_lines source JOIN ledger_accounts a ON a.id=source.account_id
                    WHERE source.operation_id=$1 AND a.purpose='client_wallet') AND o.id>$1 AND o.status='posted' AND o.reversal_of_id IS NULL
                    AND NOT EXISTS(SELECT 1 FROM financial_operations r WHERE r.reversal_of_id=o.id AND r.status='posted'))
        """,operation.id):
            raise ReversalBlocked('Сначала отмените последующие движения этого кошелька')
        if await self._connection.fetchval('SELECT EXISTS(SELECT 1 FROM payment_watch_events WHERE operation_id=$1)',operation.id):
            raise ReversalBlocked('Подтверждённый сетевой перевод требует фактического возврата денег')
        if operation.kind=='manual_journal' and await self._connection.fetchval(
            "SELECT command_details->>'purpose'='client_zero' FROM financial_operations WHERE id=$1",operation.id):
            raise ReversalBlocked('Обнуление связано с закрытием клиента; для исправления нужен отдельный сценарий восстановления')
        # Context-specific reversals must also undo their allocations. Never reverse their GL alone.
        if not allow_business and await self._connection.fetchval("SELECT operation_has_business_document($1)", operation.id):
            raise ReversalBlocked("Документ нужно отменять через его предметный сервис")


class AccountsRepository:
    def __init__(self, connection: asyncpg.Connection):
        self._connection = connection

    async def valuation_accounts(self,instruments):
        rows=await self._connection.fetch("""
            SELECT a.id FROM ledger_accounts a JOIN posted_account_balances b ON b.account_id=a.id
            WHERE a.purpose='retained_result' OR (a.instrument_code=ANY($1::text[]) AND
                (a.purpose='cash' OR b.unvalued_native<>0)) ORDER BY a.id
        """,list(instruments))
        return tuple(row['id'] for row in rows)

    async def lock_state(self, account_ids: tuple[int, ...]) -> PostingState:
        rows = await self._connection.fetch("""
            SELECT a.*,coalesce(i.precision,CASE WHEN a.currency_code='USDT' THEN 8 ELSE c.precision END) AS native_precision
            FROM ledger_accounts a JOIN currencies c ON c.code=a.currency_code
            LEFT JOIN instruments i ON i.code=a.instrument_code
            WHERE a.id=ANY($1::bigint[]) ORDER BY a.id FOR UPDATE OF a
        """, list(account_ids))
        if len(rows) != len(account_ids):
            raise InvalidPosting("Счёт не найден")
        cash_bindings = await self._connection.fetch(
            "SELECT ledger_account_id,instrument_code FROM cash_accounts WHERE ledger_account_id=ANY($1::bigint[])",
            list(account_ids))
        bound = {row["ledger_account_id"]: row["instrument_code"] for row in cash_bindings}
        for row in rows:
            if row["purpose"] == "cash" and (bound.get(row["id"]) != row["instrument_code"]
                    or row["account_class"] != "asset" or row["normal_side"] != "debit"):
                raise InvalidPosting("Кассовый счёт должен быть привязан к инструменту общей кассы")
        balances = await self._connection.fetch("SELECT * FROM posted_account_balances WHERE account_id=ANY($1::bigint[])", list(account_ids))
        by_id = {row["account_id"]: row for row in balances}
        with localcontext() as context:
            context.prec = 80
            states = tuple(AccountState(row["id"], row["currency_code"], row["instrument_code"],
                row["native_precision"], Side(row["normal_side"]), row["purpose"], row["is_active"],
                by_id[row["id"]]["balance"] * (1 if row["normal_side"] == 'debit' else -1),
                by_id[row["id"]]["carrying_usdt"] * (1 if row["normal_side"] == 'debit' else -1),
                by_id[row['id']]['unvalued_native']*(1 if row['normal_side']=='debit' else -1)) for row in rows)
        instruments = sorted(row.instrument for row in states if row.purpose == "cash")
        if instruments:
            await self._connection.fetch("SELECT instrument_code FROM inventory_balances WHERE instrument_code=ANY($1::text[]) ORDER BY instrument_code FOR UPDATE", instruments)
        positions = tuple(InventoryPosition(a.instrument, a.native, a.usdt,a.unvalued) for a in states if a.purpose == "cash")
        return PostingState(states, positions)

    async def apply(self, operation_id: int, plan: PostingPlan, state: PostingState, *, reversing=False) -> None:
        with localcontext() as context:
            context.prec = 80
            await self._apply(operation_id, plan, state, reversing=reversing)

    async def _apply(self, operation_id: int, plan: PostingPlan, state: PostingState, *, reversing=False) -> None:
        positions = {p.instrument: p for p in state.positions}
        deltas = {a.id: [Decimal(0), Decimal(0),Decimal(0)] for a in state.accounts}
        for number, line in enumerate(plan.lines, 1):
            account = state.account(line.account_id)
            signed_quantity, signed_cost = line.side.sign * line.native, line.side.sign * line.usdt
            deltas[account.id][0] += signed_quantity
            deltas[account.id][1] += signed_cost
            deltas[account.id][2] += line.side.sign*line.unvalued
            if account.purpose != "cash":
                continue
            position = positions[account.instrument]
            if not reversing and line.side == Side.CREDIT and position.withdrawal_cost(line.native,unvalued=line.unvalued) != line.usdt:
                raise InvalidPosting("Списание кассы должно использовать её себестоимость")
            new = replace(position, quantity=position.quantity + signed_quantity, cost=position.cost + signed_cost,
                unvalued=position.unvalued+line.side.sign*line.unvalued)
            exact(new.quantity, account.precision)
            exact(new.cost)
            if new.quantity < 0 or new.cost < 0:
                raise InsufficientCash("Касса не может быть отрицательной")
            if new.unvalued<0 or new.unvalued>new.quantity:
                raise InvalidPosting('Некорректный неоценённый остаток кассы')
            if new.quantity == 0 and new.cost != 0:
                raise InvalidPosting("Пустая касса должна иметь нулевую стоимость")
            positions[account.instrument] = new
            await self._connection.execute("""
                INSERT INTO inventory_movements VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """, operation_id, number, account.instrument, signed_quantity, signed_cost, new.quantity, new.cost,
                line.side.sign*line.unvalued,new.unvalued)
        for account in state.accounts:
            quantity, cost,unvalued = deltas[account.id]
            await self._connection.execute("""
                INSERT INTO account_balances(account_id,debit_signed_native,debit_signed_usdt,version,debit_signed_unvalued)
                VALUES ($1,$2,$3,1,$4) ON CONFLICT(account_id) DO UPDATE SET
                    debit_signed_native=EXCLUDED.debit_signed_native,
                    debit_signed_usdt=EXCLUDED.debit_signed_usdt,version=account_balances.version+1,
                    debit_signed_unvalued=EXCLUDED.debit_signed_unvalued
            """, account.id, account.native + quantity, account.usdt + cost,account.unvalued+unvalued)
        for position in positions.values():
            await self._connection.execute("""
                UPDATE inventory_balances SET quantity=$2,cost_usdt=$3,version=version+1,unvalued_quantity=$4 WHERE instrument_code=$1
            """, position.instrument, position.quantity, position.cost,position.unvalued)

    async def rebuild(self) -> None:
        await self._connection.execute("""
            INSERT INTO account_balances(account_id,debit_signed_native,debit_signed_usdt,version,debit_signed_unvalued)
            SELECT a.id,coalesce(sum(CASE l.side WHEN 'debit' THEN l.native_amount ELSE -l.native_amount END),0),
                coalesce(sum(CASE l.side WHEN 'debit' THEN l.amount_usdt ELSE -l.amount_usdt END),0),1,
                coalesce(sum(CASE l.side WHEN 'debit' THEN l.unvalued_native ELSE -l.unvalued_native END),0)
            FROM ledger_accounts a LEFT JOIN journal_lines l ON l.account_id=a.id
                AND EXISTS(SELECT 1 FROM financial_operations o WHERE o.id=l.operation_id AND o.status='posted')
            GROUP BY a.id ON CONFLICT(account_id) DO UPDATE SET debit_signed_native=EXCLUDED.debit_signed_native,
                debit_signed_usdt=EXCLUDED.debit_signed_usdt,version=account_balances.version+1,
                debit_signed_unvalued=EXCLUDED.debit_signed_unvalued
        """)
        await self._connection.execute("""
            INSERT INTO inventory_balances(instrument_code,quantity,cost_usdt,version,unvalued_quantity)
            SELECT c.instrument_code,b.balance,b.carrying_usdt,1,b.unvalued_native FROM cash_accounts c
                JOIN posted_account_balances b ON b.account_id=c.ledger_account_id
            ON CONFLICT(instrument_code) DO UPDATE SET quantity=EXCLUDED.quantity,
                cost_usdt=EXCLUDED.cost_usdt,version=inventory_balances.version+1,unvalued_quantity=EXCLUDED.unvalued_quantity
        """)


class OutboxRepository:
    def __init__(self, connection: asyncpg.Connection):
        self._connection = connection

    async def enqueue(self, operation: PostedOperation) -> None:
        await self._connection.execute("""
            INSERT INTO telegram_outbox(operation_id,event_key,event_type,payload)
            VALUES ($1,$2,'finance.operation_posted',$3::jsonb)
        """, operation.id, f"finance:posted:{operation.id}",
            canonical_json({"operation_id": operation.id, "kind": operation.kind,
                            "reversal_of": operation.reversal_of}))


class DocumentNotesRepository:
    def __init__(self, connection: asyncpg.Connection):
        self._connection = connection

    async def save_note(self, operation_id: int, note: str) -> None:
        if not note.strip():
            raise InvalidPosting("Примечание не может быть пустым")
        await self._connection.execute("INSERT INTO operation_audit_notes VALUES ($1,$2)", operation_id, note)
