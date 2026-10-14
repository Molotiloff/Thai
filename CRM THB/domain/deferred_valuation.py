"""Deferred client THB funds are usable cash, with no invented deposit quote."""
from dataclasses import replace
from decimal import Decimal

from domain.finance.models import (
    FxQuote,
    InvalidPosting,
    InventoryPosition,
    JournalLine,
    PostingState,
    Side,
    Valuation,
)
from domain.wallet_valuation import liability_change


def cash_wallet_change(wallet, change, quote):
    balance,carrying,unvalued=-wallet.native,-wallet.usdt,-wallet.unvalued
    if change>0 and quote is None:
        offset=min(change,max(-balance,Decimal(0)))
        value=liability_change(balance,carrying,offset,None) if offset else Decimal(0)
        return value,change-offset
    offset=min(-change,max(unvalued,Decimal(0))) if change<0 else Decimal(0)
    valued_change=change+offset
    value=liability_change(balance-unvalued,carrying,valued_change,quote) if valued_change else Decimal(0)
    return value,-offset


def price_pending(state: PostingState, quotes: tuple[FxQuote,...]):
    """A balanced prefix of the real operation; no separate profit or native movement."""
    accounts={a.id:a for a in state.accounts}
    lines=[]
    for quote in quotes:
        if quote.currency!='THB' or quote.instrument is None:
            continue
        wallets=[a for a in accounts.values() if a.instrument==quote.instrument
                 and a.purpose=='client_wallet' and a.unvalued<0]
        if not wallets:
            continue
        cash=next((a for a in accounts.values() if a.instrument==quote.instrument and a.purpose=='cash'),None)
        if cash is None or cash.unvalued!=sum((-a.unvalued for a in wallets),Decimal(0)):
            raise InvalidPosting('Неоценённые средства кассы и клиентов не совпадают')
        for wallet in wallets:
            quantity=-wallet.unvalued
            cost=quote.value(quantity)
            batch=(
                JournalLine(cash.id,'THB',Side.CREDIT,quantity,Decimal(0),Valuation.HISTORICAL,unvalued=quantity),
                JournalLine(cash.id,'THB',Side.DEBIT,quantity,cost,Valuation.RATE,quote),
                JournalLine(wallet.id,'THB',Side.DEBIT,quantity,Decimal(0),Valuation.HISTORICAL,unvalued=quantity),
                JournalLine(wallet.id,'THB',Side.CREDIT,quantity,cost,Valuation.RATE,quote),
            )
            batch=tuple(replace(line,valuation_component=True) for line in batch)
            lines.extend(batch)
            for line in batch:
                account=accounts[line.account_id]
                accounts[account.id]=replace(account,native=account.native+line.side.sign*line.native,
                    usdt=account.usdt+line.side.sign*line.usdt,unvalued=account.unvalued+line.side.sign*line.unvalued)
    updated=tuple(accounts.values())
    return tuple(lines),PostingState(updated,tuple(InventoryPosition(a.instrument,a.native,a.usdt,a.unvalued)
        for a in updated if a.purpose=='cash'))
