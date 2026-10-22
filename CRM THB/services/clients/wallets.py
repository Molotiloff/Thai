from typing import cast

from domain.deferred_valuation import cash_wallet_change
from domain.finance.fx_result import balance_with_fx
from domain.finance.models import (
    FxQuote,
    InvalidPosting,
    JournalLine,
    OperationCommand,
    Side,
    Valuation,
    canonical_json,
    decimal,
    exact,
    rounded,
)
from services.clients.ports import ClientUnitOfWork, WalletReadPort
from services.exchange.ledger import TradingUnitOfWork
from services.finance.posting import CallbackDocument, OperationPostingService


class WalletCashService:
    """One ordinary cash/wallet path for all client groups, including partners."""
    def __init__(self, posting: OperationPostingService, queries: WalletReadPort):
        self._posting, self._queries = posting, queries

    async def deposit(self, **kwargs):
        return await self._cash_change(deposit=True, **kwargs)

    async def withdraw(self, **kwargs):
        return await self._cash_change(deposit=False, **kwargs)

    async def _cash_change(self, *, deposit, key, actor_id, client_id, instrument,
                           quantity, business_date, effective_at, quoted_rate=None, comment='', request_id=None, work=None, payment_watch_id=None, document=None):
        accounts = await self._queries.wallet_accounts(client_id, instrument)
        quantity = exact(decimal(quantity), accounts['precision'])
        if quantity <= 0:
            raise InvalidPosting('Сумма должна быть положительной')
        quote = None
        if accounts['currency'] == 'USDT' or quoted_rate is not None:
            quote = FxQuote(accounts['currency'], decimal(quoted_rate if quoted_rate is not None else 1),
                'units_per_usdt' if accounts['currency']=='THB' else 'usdt_per_unit', instrument)
        if quote is None:
            quote = await self._queries.internal_quote(instrument,effective_at,key)
        details = {'client': client_id, 'instrument': instrument, 'quantity': quantity,
                   'quoted_rate': quote.quoted_rate if quote else None, 'comment': comment}
        if request_id is not None:
            details['request_id'] = request_id
        if payment_watch_id is not None:
            details['payment_watch_id'] = payment_watch_id
        command = OperationCommand(key, 'wallet_deposit' if deposit else 'wallet_withdrawal', actor_id,
            business_date, effective_at, tuple(sorted(accounts[name] for name in ('cash','wallet','fx_income','fx_loss'))),
            canonical_json(details))
        async def prepare(work):
            await cast(ClientUnitOfWork, work).clients.lock_client(client_id)
            if request_id is not None:
                request = await cast(TradingUnitOfWork, work).trades.lock(request_id)
                attrs = request['attributes']
                if (request['kind'] != ('deposit' if deposit else 'withdrawal') or request['status'] != 'ready'
                    or request['client_id'] != client_id or attrs['instrument'] != instrument
                    or decimal(attrs['quantity']) != quantity):
                    raise InvalidPosting('Кассовая операция не соответствует готовой заявке')
        async def save(operation_id, work, plan):
            await cast(TradingUnitOfWork, work).trades.complete_cash_request(request_id, operation_id)
        def build(state):
            wallet = state.account(accounts['wallet'])
            if not deposit and quantity>state.position(instrument).quantity:
                state.position(instrument).withdrawal_cost(quantity)
            signed = quantity if deposit else -quantity
            principal_delta,pending_delta=cash_wallet_change(wallet,signed,quote)
            liability = abs(principal_delta)
            unvalued=abs(pending_delta)
            cost = (quote.value(quantity) if quote else liability) if deposit else state.position(instrument).withdrawal_cost(quantity,unvalued=min(quantity,state.position(instrument).unvalued))
            cash_pending = pending_delta if deposit else -min(quantity,state.position(instrument).unvalued)
            lines = [
                JournalLine(accounts['cash'], accounts['currency'], Side.DEBIT if deposit else Side.CREDIT,
                    quantity, cost, Valuation.RATE if deposit and quote else Valuation.CARRYING, quote if deposit else None,unvalued=abs(cash_pending)),
                JournalLine(accounts['wallet'], accounts['currency'], Side.CREDIT if deposit else Side.DEBIT,
                    quantity, liability, Valuation.CARRYING,unvalued=unvalued),
            ]
            return balance_with_fx(tuple(lines), income_account_id=accounts['fx_income'], loss_account_id=accounts['fx_loss'])
        def valuation_quotes(state):
            if quote is not None:
                return (quote,)
            wallet=state.account(accounts['wallet'])
            valued_balance=-wallet.native+wallet.unvalued
            if not deposit and accounts['currency']!='USDT' and quantity> -wallet.unvalued and valued_balance>0 and wallet.usdt<0:
                return (FxQuote(accounts['currency'],rounded(valued_balance/(-wallet.usdt) if accounts['currency']=='THB' else (-wallet.usdt)/valued_balance,18),'units_per_usdt' if accounts['currency']=='THB' else 'usdt_per_unit',instrument),)
            return ()
        return await self._posting.post(command, build, prepare=prepare,
            document=CallbackDocument(save) if request_id is not None else document, work=work,valuation_quotes=valuation_quotes)
