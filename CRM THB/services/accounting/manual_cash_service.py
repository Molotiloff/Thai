from __future__ import annotations

from domain.finance.models import (
    InvalidPosting,
    JournalLine,
    OperationCommand,
    PostingPlan,
    Side,
    Valuation,
    canonical_json,
    decimal,
    exact,
)
from services.finance.ports import CashAdjustmentReadPort, FinanceUowFactory
from services.finance.posting import OperationPostingService, ReversalService
from services.metadata_commands import MetadataCommandService
from services.unit_of_work import UnitOfWorkFactory

from .models import (
    ManualCashAccount,
    ManualCashMove,
    RecordManualCashMove,
    ReverseManualCashMove,
)


class ManualCashService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory | None = None, *, posting: OperationPostingService | None = None, queries: CashAdjustmentReadPort | None = None, reversals: ReversalService | None = None, ledger_uow_factory: FinanceUowFactory | None = None) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._posting,self._queries,self._reversals=posting,queries,reversals
        self._commands=MetadataCommandService(ledger_uow_factory) if ledger_uow_factory else None

    async def adjust(self, *, key, actor_id, instrument, quantity, business_date, effective_at, chat_id, comment=''):
        accounts=await self._queries.cash_adjustment_accounts(instrument)
        quantity=exact(decimal(quantity),accounts['precision'])
        if not quantity:
            raise InvalidPosting('Изменение кассы должно быть ненулевым')
        quote=await self._queries.internal_quote(instrument,effective_at,key)
        command=OperationCommand(key,'manual_journal',actor_id,business_date,effective_at,
            tuple(sorted((accounts['cash'],accounts['offset']))),canonical_json({
                'purpose':'cash_adjustment','instrument':instrument,'quantity':quantity,
                'quoted_rate':quote.quoted_rate if quote else None,'chat_id':chat_id,'comment':comment}))
        async def prepare(work):
            await work.journal.guard(command,'cash.execute')
        def build(state):
            side=Side.DEBIT if quantity>0 else Side.CREDIT
            native=abs(quantity)
            if quantity>0:
                pending=native if quote is None else decimal(0)
                cost=quote.value(native) if quote else decimal(0)
            else:
                position=state.position(instrument)
                pending=min(native,position.unvalued)
                cost=position.withdrawal_cost(native,unvalued=pending)
            return PostingPlan((
                JournalLine(accounts['cash'],accounts['currency'],side,native,cost,Valuation.RATE if quantity>0 and quote else Valuation.CARRYING,quote if quantity>0 else None,unvalued=pending),
                JournalLine(accounts['offset'],accounts['currency'],side.opposite,native,cost,Valuation.CARRYING,unvalued=pending)))
        return await self._posting.post(command,build,prepare=prepare,valuation_quotes=(quote,) if quote else ())

    async def undo_by_message(self, *, chat_id, message_id, actor_id, business_date, effective_at):
        operation_id=await self._queries.cash_operation_by_message(chat_id,message_id)
        if operation_id is None:
            raise InvalidPosting('Оригинальная корректировка кассы не найдена')
        async def action(work):
            previous=await work.journal.find_by_key(f'cash-undo:{operation_id}')
            if previous is None:
                previous=await self._reversals.reverse(original_id=operation_id,key=f'cash-undo:{operation_id}',
                    actor_id=actor_id,business_date=business_date,effective_at=effective_at,
                    reason='Отмена корректировки общей кассы в Telegram',work=work)
            return {'operation_id':previous.id,'original_id':operation_id}
        return await self._commands.execute(key=f'cash-undo-command:{operation_id}',actor_id=actor_id,
            kind='cash_undo',permission='finance.reverse',payload={'operation_id':operation_id},action=action)

    async def snapshot(
        self, *, limit: int = 20
    ) -> tuple[tuple[ManualCashAccount, ...], tuple[ManualCashMove, ...]]:
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.manual_cash.snapshot(limit=limit)
            await unit_of_work.commit()
            return result

    async def record(self, command: RecordManualCashMove) -> ManualCashMove:
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.manual_cash.record(command)
            await unit_of_work.commit()
            return result

    async def reverse(self, command: ReverseManualCashMove) -> ManualCashMove:
        async with self._unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.manual_cash.reverse(command)
            await unit_of_work.commit()
            return result
