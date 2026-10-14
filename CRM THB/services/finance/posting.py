from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

from domain.deferred_valuation import price_pending
from domain.finance.models import (
    IdempotencyConflict,
    InvalidPosting,
    JournalLine,
    OperationCommand,
    PostedOperation,
    PostingPlan,
    PostingState,
    Side,
    Valuation,
    canonical_json,
    exact,
)
from services.finance.ports import (
    DocumentWriter,
    FinanceUnitOfWork,
    FinanceUowFactory,
    PlanBuilder,
    Preparation,
)


class CallbackDocument:
    def __init__(self, save: Callable[[int, FinanceUnitOfWork, PostingPlan], Awaitable[None]]):
        self._save = save

    async def save(self, operation_id: int, work: FinanceUnitOfWork, plan: PostingPlan) -> None:
        await self._save(operation_id, work, plan)


def base_line(account: int, side: Side, amount: Decimal) -> JournalLine:
    return JournalLine(account, "USDT", side, amount, amount, Valuation.BASE)


class OperationPostingService:
    def __init__(self, uow_factory: FinanceUowFactory):
        self._uow_factory = uow_factory

    async def post(self, command: OperationCommand, builder: PlanBuilder, *,
                   document: DocumentWriter | None = None, prepare: Preparation | None = None,
                   work: FinanceUnitOfWork | None = None, valuation_quotes=()) -> PostedOperation:
        """A supplied UoW joins a compound command; its caller owns commit/rollback."""
        if work is not None:
            return await self._post(command, builder, document=document, prepare=prepare, uow=work,valuation_quotes=valuation_quotes)
        async with self._uow_factory() as uow:
            result = await self._post(command, builder, document=document, prepare=prepare, uow=uow,valuation_quotes=valuation_quotes)
            await uow.commit()
            return result

    async def _post(self, command, builder, *, document, prepare, uow,valuation_quotes):
        await uow.journal.guard(command, "finance.post")
        previous = await uow.journal.find_by_key(command.key)
        if previous is not None:
            if previous.fingerprint != command.fingerprint:
                raise IdempotencyConflict("Ключ уже использован для другой команды")
            return previous
        if prepare is not None:
            await prepare(uow)
        state = await uow.accounts.lock_state(command.accounts)
        quotes=tuple(valuation_quotes(state) if callable(valuation_quotes) else valuation_quotes)
        instruments=tuple(sorted({q.instrument for q in quotes if q.currency=='THB' and q.instrument}))
        if instruments:
            extra=await uow.accounts.valuation_accounts(instruments)
            state=await uow.accounts.lock_state(tuple(sorted(set(command.accounts)|set(extra)))) if extra else state
        prefix,valued=price_pending(state,quotes)
        plan = builder(valued)
        if plan.mode != command.mode or not {line.account_id for line in plan.lines}.issubset(command.accounts):
            raise InvalidPosting("План не соответствует режиму или счетам команды")
        if prefix:
            plan=PostingPlan(prefix+plan.lines,plan.mode)
        for line in plan.lines:
            account = state.account(line.account_id)
            if not account.active or account.currency != line.currency:
                raise InvalidPosting("Счёт неактивен или валюта строки не совпадает")
            exact(line.native, account.precision)
        operation_id = await uow.journal.create(command)
        await uow.journal.save_lines(operation_id, plan, command)
        if document is not None:
            await document.save(operation_id, uow, plan)
        await uow.accounts.apply(operation_id, plan, state)
        result = await uow.journal.finish(operation_id)
        await uow.outbox.enqueue(result)
        return result


class CashTransferService:
    def __init__(self, posting: OperationPostingService):
        self._posting = posting

    async def transfer(self, *, key: str, actor_id: int, business_date: date,
                       effective_at: datetime, source_id: int, target_id: int,
                       quantity: Decimal) -> PostedOperation:
        if source_id == target_id or quantity <= 0:
            raise InvalidPosting("Нужны разные кассовые счета и положительная сумма")
        command = OperationCommand(key, "cash_transfer", actor_id, business_date, effective_at,
            tuple(sorted((source_id, target_id))), canonical_json({"source": source_id,
                "target": target_id, "quantity": quantity}))

        def build(state: PostingState) -> PostingPlan:
            source, target = state.account(source_id), state.account(target_id)
            if source.purpose != "cash" or target.purpose != "cash" or source.currency != target.currency:
                raise InvalidPosting("Перевод возможен между кассами одной валюты")
            cost = state.position(source.instrument).withdrawal_cost(quantity)
            return PostingPlan((
                JournalLine(source_id, source.currency, Side.CREDIT, quantity, cost, Valuation.CARRYING),
                JournalLine(target_id, target.currency, Side.DEBIT, quantity, cost, Valuation.CARRYING),
            ))

        return await self._posting.post(command, build)


class ReversalService:
    def __init__(self, uow_factory: FinanceUowFactory):
        self._uow_factory = uow_factory

    async def reverse(self, *, original_id: int, key: str, actor_id: int,
                      business_date: date, effective_at: datetime, reason: str,
                      prepare: Preparation | None = None, details: dict | None = None,
                      document: DocumentWriter | None = None,
                      work: FinanceUnitOfWork | None = None) -> PostedOperation:
        """Reuse the same reversal engine inside an optional caller-owned transaction."""
        options = {'original_id':original_id, 'key':key, 'actor_id':actor_id,
            'business_date':business_date, 'effective_at':effective_at, 'reason':reason,
            'prepare':prepare, 'details':details, 'document':document}
        if work is not None:
            return await self._reverse(**options, uow=work)
        async with self._uow_factory() as uow:
            result = await self._reverse(**options, uow=uow)
            await uow.commit()
            return result

    async def _reverse(self, *, original_id, key, actor_id, business_date, effective_at,
                       reason, prepare, details, document, uow):
        original = await uow.journal.get(original_id)
        rows = await uow.journal.lines(original_id)
        command = OperationCommand(key, "reversal", actor_id, business_date, effective_at,
            tuple(sorted({line.account_id for line in rows})), canonical_json({**(details or {}), "original": original_id}),
            context=original.context, mode=original.mode, reversal_of=original_id, reason=reason)
        await uow.journal.guard(command, "finance.reverse")
        previous = await uow.journal.find_by_key(key)
        if previous is not None:
            if previous.fingerprint != command.fingerprint:
                raise IdempotencyConflict("Ключ сторно уже использован")
            return previous
        if prepare is not None:
            await prepare(uow)
        state = await uow.accounts.lock_state(command.accounts)
        await uow.journal.check_reversible(original, allow_business=prepare is not None)
        plan = PostingPlan(tuple(replace(line, side=line.side.opposite, quote=None,
                                         valuation=Valuation.HISTORICAL) for line in reversed(rows)), original.mode)
        operation_id = await uow.journal.create(command)
        await uow.journal.save_lines(operation_id, plan, command)
        if document is not None:
            await document.save(operation_id, uow, plan)
        await uow.accounts.apply(operation_id, plan, state, reversing=True)
        result = await uow.journal.finish(operation_id)
        await uow.outbox.enqueue(result)
        return result


class ProjectionRebuildService:
    def __init__(self, uow_factory: FinanceUowFactory):
        self._uow_factory = uow_factory

    async def rebuild(self, actor_id: int) -> None:
        async with self._uow_factory() as uow:
            await uow.journal.guard_rebuild(actor_id)
            await uow.accounts.rebuild()
            await uow.commit()
