"""Deferred client currency funds are usable cash, with no invented deposit quote."""
from dataclasses import replace
from decimal import Decimal

from domain.finance.models import (
    FxQuote,
    InventoryPosition,
    JournalLine,
    PostingState,
    Side,
    Valuation,
    rounded,
)
from domain.wallet_valuation import liability_change


def cash_wallet_change(wallet, change, quote):
    """Offset unknown quantities, then valued debt, and defer any unknown remainder."""
    balance,carrying,unknown=-wallet.native,-wallet.usdt,-wallet.unvalued
    sign=Decimal(1) if change>0 else Decimal(-1)
    pending=sign*min(abs(change),abs(unknown)) if change*unknown<0 else Decimal(0)
    remaining=change-pending
    valued_balance=balance-unknown
    offset=sign*min(abs(remaining),abs(valued_balance)) if remaining*valued_balance<0 else Decimal(0)
    value=liability_change(valued_balance,carrying,offset,None) if offset else Decimal(0)
    remainder=remaining-offset
    if remainder:
        if quote is None:
            pending+=remainder
        else:
            value+=sign*quote.value(abs(remainder))
    return value,pending


def transfer_wallet_value(sender, recipient, quantity, quote):
    """Move the sender's valued and unknown portions without inventing a rate."""
    source_value,source_pending=cash_wallet_change(sender,-quantity,quote)
    principal=-source_value
    pending_quantity=abs(source_pending)
    value=pending=Decimal(0)
    target=recipient
    if pending_quantity:
        value,pending=cash_wallet_change(target,pending_quantity,None)
        target=replace(target,native=target.native-pending_quantity,usdt=target.usdt-value,
            unvalued=target.unvalued-pending)
    valued_quantity=quantity-pending_quantity
    if valued_quantity:
        carry=FxQuote(sender.currency,rounded(valued_quantity/principal if sender.currency=='THB' else principal/valued_quantity,18),'units_per_usdt' if sender.currency=='THB' else 'usdt_per_unit',recipient.instrument) if principal else quote
        second_value,second_pending=cash_wallet_change(target,valued_quantity,carry)
        value+=second_value
        pending+=second_pending
    return principal,source_pending,value,pending


def price_pending(state: PostingState, quotes: tuple[FxQuote,...]):
    """Resolve unknown quantities at the real exchange's rate, preserving native balances."""
    accounts={a.id:a for a in state.accounts}
    lines=[]
    for quote in quotes:
        if quote.currency=='USDT' or quote.instrument is None:
            continue
        pending=[a for a in accounts.values() if a.instrument==quote.instrument and a.unvalued]
        signed_value=Decimal(0)
        for account in pending:
            quantity=abs(account.unvalued)
            side=Side.DEBIT if account.unvalued>0 else Side.CREDIT
            value=quote.value(quantity)
            batch=(
                JournalLine(account.id,account.currency,side.opposite,quantity,Decimal(0),Valuation.HISTORICAL,unvalued=quantity,valuation_component=True),
                JournalLine(account.id,account.currency,side,quantity,value,Valuation.RATE,quote,valuation_component=True),
            )
            lines.extend(batch)
            signed_value+=side.sign*value
            accounts[account.id]=replace(account,usdt=account.usdt+side.sign*value,unvalued=Decimal(0))
        if signed_value:
            equity=next(a for a in accounts.values() if a.purpose=='retained_result')
            side=Side.CREDIT if signed_value>0 else Side.DEBIT
            value=abs(signed_value)
            lines.append(JournalLine(equity.id,'USDT',side,value,value,Valuation.BASE))
            accounts[equity.id]=replace(equity,native=equity.native+side.sign*value,usdt=equity.usdt+side.sign*value)
    updated=tuple(accounts.values())
    return tuple(lines),PostingState(updated,tuple(InventoryPosition(a.instrument,a.native,a.usdt,a.unvalued)
        for a in updated if a.purpose=='cash'))
