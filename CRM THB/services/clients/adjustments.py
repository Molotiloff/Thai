"""Audited manual wallet corrections, independent of physical cash and profit."""

from typing import cast

from domain.deferred_valuation import cash_wallet_change
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
    revision_details,
)
from services.clients.ports import ClientUnitOfWork, WalletAuditReadPort
from services.finance.ports import FinanceUowFactory
from services.finance.posting import OperationPostingService
from services.metadata_commands import MetadataCommandService


class WalletAdjustmentService:
    def __init__(self, posting: OperationPostingService, queries: WalletAuditReadPort,
                 uow_factory: FinanceUowFactory | None = None):
        self._posting, self._queries = posting, queries
        self._commands = MetadataCommandService(uow_factory) if uow_factory else None

    async def adjust(self, *, key, actor_id, client_id, instrument, quantity,
                     business_date, effective_at, quoted_rate=None, comment="", profit_revision_id=None):
        accounts = await self._queries.adjustment_accounts(client_id, instrument)
        quantity = exact(decimal(quantity), accounts["precision"])
        if quantity == 0:
            raise InvalidPosting("Изменение кошелька должно быть ненулевым")
        quote = await self._queries.internal_quote(instrument,effective_at,key)
        command = OperationCommand(
            key, "manual_journal", actor_id, business_date, effective_at,
            tuple(sorted((accounts["wallet"], accounts["adjustment_offset"]))),
            canonical_json(revision_details({"client": client_id, "instrument": instrument,
                            "quantity": quantity, "quoted_rate": quote.quoted_rate if quote else None,
                            "comment": comment, "purpose": "wallet_adjustment"},profit_revision_id)),
        )

        async def prepare(work):
            await cast(ClientUnitOfWork, work).clients.lock_client(client_id)

        def build(state):
            wallet = state.account(accounts["wallet"])
            delta, pending = cash_wallet_change(wallet, quantity, quote)
            side = Side.CREDIT if quantity > 0 else Side.DEBIT
            value = delta.copy_abs()
            return PostingPlan((
                JournalLine(wallet.id, wallet.currency, side, quantity.copy_abs(), value, Valuation.CARRYING,unvalued=abs(pending)),
                JournalLine(accounts["adjustment_offset"], wallet.currency, side.opposite, quantity.copy_abs(), value, Valuation.CARRYING,unvalued=abs(pending)),
            ))

        return await self._posting.post(command, build, prepare=prepare,valuation_quotes=(quote,) if quote else ())


    async def prepare_zero(self, *, key, actor_id, client_id):
        async def action(work):
            await work.clients.lock_client(client_id)
            preview = await work.clients.zero_preview(client_id)
            return {**preview,'actor_id':actor_id}
        return await self._commands.execute(key=key,actor_id=actor_id,kind='wallet_zero_request',
            payload={'client_id':client_id},action=action)

    async def resolve_zero(self, *, key, actor_id, confirmed, business_date, effective_at,
                           profit_revision_id=None):
        request = await self._queries.directory_event(key)
        if not {'actor_id','client_id','fingerprint','wallets'}.issubset(request):
            raise InvalidPosting('Это не запрос обнуления клиента')
        if request['actor_id']!=actor_id:
            raise InvalidPosting('Подтвердить может только сотрудник, создавший запрос')
        async def action(work):
            if not confirmed:
                return {'cancelled':True,'operation_id':None,'client_id':request['client_id']}
            await work.journal.lock_posting_period(profit_revision_id=profit_revision_id)
            await work.clients.lock_client(request['client_id'])
            offsets={}
            for row in request['wallets']:
                accounts=await self._queries.adjustment_accounts(request['client_id'],row['instrument_code'])
                offsets[row['id']]=accounts['adjustment_offset']
            account_ids=tuple(sorted({*offsets.values(),*(row['id'] for row in request['wallets'])}))
            # Lock every wallet before checking the preview, including history versions (ABA protection).
            await work.accounts.lock_state(account_ids)
            preview = await work.clients.zero_preview(request['client_id'])
            if preview['fingerprint']!=request['fingerprint']:
                raise InvalidPosting('Балансы или история изменились. Повторите /обнулить и проверьте суммы')
            nonzero = [row for row in preview['wallets'] if row['balance'] or row['carrying_usdt']]
            operation = None
            if nonzero:
                command = OperationCommand(key+':post','manual_journal',actor_id,business_date,effective_at,
                    account_ids,canonical_json(revision_details({'purpose':'client_zero',
                        'client':request['client_id'],'fingerprint':request['fingerprint'],
                        'comment':'/обнулить: корректировка и закрытие клиента'},profit_revision_id)))
                def build(state):
                    lines = []
                    # Native corrections use the same contra accounts as ordinary wallet adjustments.
                    for row in nonzero:
                        wallet = state.account(row['id'])
                        side = Side.CREDIT if wallet.native>0 else Side.DEBIT
                        value = wallet.usdt.copy_abs()
                        lines.append(JournalLine(wallet.id,wallet.currency,side,wallet.native.copy_abs(),value,Valuation.CARRYING,unvalued=abs(wallet.unvalued)))
                        lines.append(JournalLine(offsets[wallet.id],wallet.currency,side.opposite,wallet.native.copy_abs(),value,Valuation.CARRYING,unvalued=abs(wallet.unvalued)))
                    return PostingPlan(tuple(lines))
                operation = await self._posting.post(command,build,work=work)
            await work.clients.close_client(request['client_id'])
            return {'cancelled':False,'operation_id':operation.id if operation else None,'client_id':request['client_id']}
        return await self._commands.execute(key=key+':resolve',actor_id=actor_id,kind='wallet_zero_resolve',
            payload={'request':key,'confirmed':confirmed},action=action)
