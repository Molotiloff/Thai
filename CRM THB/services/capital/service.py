from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import cast
from zoneinfo import ZoneInfo

from domain.capital import fifo, interest_amount, monthly_percent
from domain.finance.fx_result import balance_with_fx
from domain.finance.models import (
    FxQuote,
    InvalidPosting,
    JournalLine,
    LedgerContext,
    OperationCommand,
    PostingMode,
    PostingPlan,
    Side,
    Valuation,
    canonical_json,
    decimal,
    exact,
    revision_details,
    rounded,
)
from services.capital.ports import CapitalReadPort, CapitalUnitOfWork
from services.finance.posting import OperationPostingService, ReversalService


class _Document:
    def __init__(self, save):
        self._save = save

    async def save(self, operation_id, uow, plan):
        await self._save(operation_id, cast(CapitalUnitOfWork, uow), plan)


class CapitalService:
    """Capital use cases coordinate ports; calculations and posting remain reusable."""
    def __init__(self, posting: OperationPostingService, reversals: ReversalService,
                 queries: CapitalReadPort):
        self._posting, self._reversals, self._queries = posting, reversals, queries

    async def create_agreement(self, *, key, actor_id, business_date, effective_at,
                               monthly_interest_percent, name=None, party_id=None):
        percent = monthly_percent(monthly_interest_percent)
        command = OperationCommand(key, 'capital_agreement', actor_id, business_date, effective_at, (),
            canonical_json({'percent': percent, 'name': name, 'party': party_id}), mode=PostingMode.MEMORANDUM)
        async def save(operation_id, uow, plan):
            await uow.capital.permission(actor_id, 'terms.manage')
            await uow.capital.create_agreement(operation_id, actor_id, business_date, percent, name, party_id)
        return await self._posting.post(command, lambda state: PostingPlan((), PostingMode.MEMORANDUM), document=_Document(save))

    async def change_term(self, *, key, actor_id, agreement_id, valid_from, effective_at,
                          monthly_interest_percent, reason):
        if not reason.strip():
            raise InvalidPosting('Причина изменения ставки обязательна')
        percent = monthly_percent(monthly_interest_percent)
        command = OperationCommand(key, 'capital_terms', actor_id, valid_from, effective_at, (),
            canonical_json({'agreement': agreement_id, 'percent': percent, 'reason': reason}),
            context=LedgerContext.INTEREST, mode=PostingMode.MEMORANDUM)
        async def prepare(work):
            uow = cast(CapitalUnitOfWork, work)
            await uow.capital.permission(actor_id, 'terms.manage')
            await uow.capital.lock_agreement(agreement_id, valid_from)
        async def save(operation_id, uow, plan):
            await uow.capital.change_term(operation_id, agreement_id, actor_id, valid_from, percent)
            await uow.documents.save_note(operation_id, reason)
        return await self._posting.post(command, lambda state: PostingPlan((), PostingMode.MEMORANDUM),
            document=_Document(save), prepare=prepare)

    async def contribute(self, *, key, actor_id, agreement_id, instrument, quantity,
                         business_date, effective_at, quoted_rate=None, comment=''):
        accounts = await self._queries.accounts(agreement_id, instrument)
        quantity = exact(decimal(quantity), accounts['precision'])
        if quantity <= 0:
            raise InvalidPosting('Вклад должен быть положительным')
        if accounts['currency'] != 'USDT' and quoted_rate is None:
            raise InvalidPosting('Для вклада требуется ручной курс относительно USDT')
        quote = FxQuote(accounts['currency'], decimal(quoted_rate if quoted_rate is not None else 1),
            'units_per_usdt' if accounts['currency'] == 'THB' else 'usdt_per_unit', instrument)
        principal = quote.value(quantity)
        command = OperationCommand(key, 'capital_contribution', actor_id, business_date, effective_at,
            tuple(sorted((accounts['cash'], accounts['principal']))),
            canonical_json({'agreement': agreement_id, 'instrument': instrument, 'quantity': quantity,
                'rate': quote.quoted_rate, 'comment': comment}))
        async def prepare(work):
            uow = cast(CapitalUnitOfWork, work)
            await uow.capital.permission(actor_id, 'capital.post')
            await uow.capital.lock_agreement(agreement_id, business_date)
            await uow.capital.capital_day(agreement_id, business_date, effective_at)
        def build(state):
            return PostingPlan((
                JournalLine(accounts['cash'], accounts['currency'], Side.DEBIT, quantity, principal, Valuation.RATE, quote),
                JournalLine(accounts['principal'], accounts['currency'], Side.CREDIT, quantity, principal, Valuation.CARRYING)))
        async def save(operation_id, uow, plan):
            await uow.capital.contribution(operation_id, agreement_id, instrument, quantity, principal, comment)
        return await self._posting.post(command, build, document=_Document(save), prepare=prepare)

    async def withdraw(self, *, key, actor_id, agreement_id, instrument, quantity,
                       business_date, effective_at, comment=''):
        accounts = await self._queries.accounts(agreement_id, instrument)
        quantity = exact(decimal(quantity), accounts['precision'])
        command = OperationCommand(key, 'capital_withdrawal', actor_id, business_date, effective_at,
            tuple(sorted((accounts['cash'], accounts['principal'], accounts['fx_income'], accounts['fx_loss']))),
            canonical_json({'agreement': agreement_id, 'instrument': instrument, 'quantity': quantity, 'comment': comment}))
        prepared = {}
        async def prepare(work):
            uow = cast(CapitalUnitOfWork, work)
            await uow.capital.permission(actor_id, 'capital.post')
            await uow.capital.lock_agreement(agreement_id, business_date)
            await uow.capital.capital_day(agreement_id, business_date, effective_at)
            prepared['allocations'] = fifo(await uow.capital.lots(agreement_id, instrument, business_date), quantity)
        def build(state):
            principal = sum((a.principal for a in prepared['allocations']), Decimal(0))
            cost = state.position(instrument).withdrawal_cost(quantity)
            return balance_with_fx((
                JournalLine(accounts['principal'], accounts['currency'], Side.DEBIT, quantity, principal, Valuation.CARRYING),
                JournalLine(accounts['cash'], accounts['currency'], Side.CREDIT, quantity, cost, Valuation.CARRYING),
            ), income_account_id=accounts['fx_income'], loss_account_id=accounts['fx_loss'])
        async def save(operation_id, uow, plan):
            await uow.capital.withdrawal(operation_id, agreement_id, instrument, quantity, prepared['allocations'], comment)
            source_ids = {a.source_operation_id for a in prepared['allocations']}
            source_ids.update(row['id'] for row in await uow.capital.source_operations(agreement_id, business_date+timedelta(days=1))
                if row['kind'] == 'capital_withdrawal' and (row['instrument'] == instrument
                    or (row['instrument'] in {'THB_CASH', 'THB_BANK'} and instrument in {'THB_CASH', 'THB_BANK'})))
            for source_id in sorted(source_ids):
                await uow.journal.add_dependency(source_id, operation_id)
        return await self._posting.post(command, build, document=_Document(save), prepare=prepare,
            valuation_quotes=lambda state:(FxQuote(accounts['currency'],rounded(quantity/sum((a.principal for a in prepared['allocations']),Decimal(0)),18),
                'units_per_usdt',instrument),) if accounts['currency']=='THB' else ())

    async def accrue(self, *, key, actor_id, agreement_id, period, business_date,
                     effective_at, reason, approved_usdt=None, profit_revision_id=None):
        if period.day != 1 or business_date != date(period.year, period.month, monthrange(period.year, period.month)[1]):
            raise InvalidPosting('Начисление относится к последнему дню указанного месяца')
        if effective_at.tzinfo is None or effective_at.astimezone(ZoneInfo('Asia/Bangkok')).date() <= business_date:
            raise InvalidPosting('Начисление полного месяца проводится после его окончания')
        if not reason.strip():
            raise InvalidPosting('Решение менеджера должно содержать причину')
        approved = None if approved_usdt is None else exact(decimal(approved_usdt))
        if approved is not None and approved < 0:
            raise InvalidPosting('Начисление не может быть отрицательным')
        accounts = await self._queries.accounts(agreement_id, 'USDT')
        # Zero approval is a memorandum; otherwise account set is known before locking.
        mode = PostingMode.MEMORANDUM if approved == 0 else PostingMode.LEDGER
        account_ids = () if mode == PostingMode.MEMORANDUM else tuple(sorted((accounts['payable'], accounts['interest_expense'])))
        command = OperationCommand(key, 'interest_accrual', actor_id, business_date, effective_at, account_ids,
            canonical_json(revision_details({'agreement': agreement_id, 'period': period, 'approved': approved, 'reason': reason}, profit_revision_id)),
            context=LedgerContext.INTEREST, mode=mode)
        prepared = {}
        async def prepare(work):
            uow = cast(CapitalUnitOfWork, work)
            await uow.capital.permission(actor_id, 'interest.approve')
            agreement = await uow.capital.lock_agreement(agreement_id, business_date)
            basis = await uow.capital.interest_basis(agreement_id, period)
            if basis['changed'] and approved is None:
                raise InvalidPosting('Капитал или ставка изменились: менеджер должен явно указать сумму начисления')
            calculated = interest_amount(basis['amount'], basis['percent'])
            final = calculated if approved is None else approved
            if agreement['principal_kind'] == 'owner_equity' and final != 0:
                raise InvalidPosting('Собственникам выплачивается доля прибыли, а не процент по капиталу')
            if final == 0 and mode != PostingMode.MEMORANDUM:
                raise InvalidPosting('Для нулевого начисления явно укажите approved_usdt=0')
            prepared.update(basis=basis, calculated=calculated, final=final)
        def build(state):
            if mode == PostingMode.MEMORANDUM:
                return PostingPlan((), mode)
            amount = prepared['final']
            return PostingPlan((
                JournalLine(accounts['interest_expense'], 'USDT', Side.DEBIT, amount, amount, Valuation.BASE),
                JournalLine(accounts['payable'], 'USDT', Side.CREDIT, amount, amount, Valuation.BASE)))
        async def save(operation_id, uow, plan):
            await uow.capital.accrue(operation_id, agreement_id, period, prepared['basis'], prepared['calculated'], prepared['final'], reason)
            sources = {r['id'] for r in await uow.capital.source_operations(agreement_id, business_date+timedelta(days=1))}
            for source_id in sorted(sources):
                await uow.journal.add_dependency(source_id, operation_id)
        return await self._posting.post(command, build, document=_Document(save), prepare=prepare)

    async def pay_interest(self, *, key, actor_id, agreement_id, instrument, quantity,
                           business_date, effective_at, reason, quoted_rate=None):
        if not reason.strip():
            raise InvalidPosting('Для выплаты требуется решение менеджера')
        accounts = await self._queries.accounts(agreement_id, instrument)
        quantity = exact(decimal(quantity), accounts['precision'])
        if quantity <= 0 or (accounts['currency'] != 'USDT' and quoted_rate is None):
            raise InvalidPosting('Нужна положительная выплата и ручной курс валюты')
        quote = FxQuote(accounts['currency'], decimal(quoted_rate if quoted_rate is not None else 1),
            'units_per_usdt' if accounts['currency'] == 'THB' else 'usdt_per_unit', instrument)
        discharged = quote.value(quantity)
        command = OperationCommand(key, 'interest_payment', actor_id, business_date, effective_at,
            tuple(sorted((accounts['cash'], accounts['payable'], accounts['fx_income'], accounts['fx_loss']))),
            canonical_json({'agreement': agreement_id, 'instrument': instrument, 'quantity': quantity,
                'rate': quote.quoted_rate, 'reason': reason}), context=LedgerContext.INTEREST)
        prepared = {}
        async def prepare(work):
            uow = cast(CapitalUnitOfWork, work)
            await uow.capital.permission(actor_id, 'interest.approve')
            prepared['agreement'] = await uow.capital.lock_agreement(agreement_id, business_date)
            remaining, allocations = discharged, []
            for accrual in await uow.capital.unpaid(agreement_id, business_date):
                take = min(remaining, accrual['remaining_usdt'])
                allocations.append((accrual['id'], accrual['operation_id'], take))
                remaining -= take
                if remaining == 0:
                    break
            if remaining != 0 or discharged <= 0:
                raise InvalidPosting('Выплата превышает начисленный долг инвестору')
            prepared['allocations'] = allocations
        def build(state):
            cost = state.position(instrument).withdrawal_cost(quantity)
            # The manual payout quote is retained on the document; cash uses WAC.
            return balance_with_fx((
                JournalLine(accounts['payable'], 'USDT', Side.DEBIT, discharged, discharged, Valuation.BASE),
                JournalLine(accounts['cash'], accounts['currency'], Side.CREDIT, quantity, cost, Valuation.CARRYING),
            ), income_account_id=accounts['fx_income'], loss_account_id=accounts['fx_loss'])
        async def save(operation_id, uow, plan):
            await uow.capital.pay(operation_id, prepared['agreement'], instrument, quantity, discharged, prepared['allocations'], reason,
                                  quote=quote, actor_id=actor_id, effective_at=effective_at)
            for _, source_id, _ in prepared['allocations']:
                await uow.journal.add_dependency(source_id, operation_id)
        return await self._posting.post(command, build, document=_Document(save), prepare=prepare,valuation_quotes=(quote,))

    async def reverse(self, *, agreement_id, original_id, key, actor_id, business_date, effective_at, reason, profit_revision_id=None):
        async def prepare(work):
            uow = cast(CapitalUnitOfWork, work)
            await uow.capital.permission(actor_id, 'capital.post')
            await uow.capital.lock_agreement(agreement_id)
            await uow.capital.reversal_target(original_id, agreement_id)
        return await self._reversals.reverse(original_id=original_id, key=key, actor_id=actor_id,
            business_date=business_date, effective_at=effective_at, reason=reason, prepare=prepare,
            details=revision_details({'agreement': agreement_id}, profit_revision_id))
