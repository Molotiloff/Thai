from dataclasses import replace
from decimal import Decimal
from typing import cast

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
    revision_details,
    rounded,
)
from domain.wallet_valuation import liability_change
from services.clients.ports import ClientUnitOfWork


class WalletConversionService:
    def __init__(self, posting, queries, trading_queries):
        self._posting, self._queries, self._trading_queries = posting, queries, trading_queries

    async def transfer(
        self,
        *,
        key,
        actor_id,
        client_id,
        source_instrument,
        target_instrument,
        quantity,
        fee,
        business_date,
        effective_at,
        quoted_rate,
        physical=False,
        profit_revision_id=None,
    ):
        if {source_instrument, target_instrument} != {"THB_CASH", "THB_BANK"}:
            raise InvalidPosting("Перевод с комиссией поддерживает THB_CASH↔THB_BANK")
        source = await self._queries.wallet_accounts(client_id, source_instrument)
        target = await self._queries.wallet_accounts(client_id, target_instrument)
        common = await self._trading_queries.accounts(
            client_id, source_instrument, target_instrument, "wallet"
        )
        quantity, fee = exact(decimal(quantity), 2), exact(decimal(fee), 2)
        if quantity <= 0 or fee < 0:
            raise InvalidPosting("Нужна положительная сумма и неотрицательная комиссия")
        quote = FxQuote("THB", decimal(quoted_rate), "units_per_usdt", source_instrument)
        ids = {
            source["wallet"],
            target["wallet"],
            common["transfer_fee_income"],
            source["fx_income"],
            source["fx_loss"],
        }
        if physical:
            ids |= {source["cash"], target["cash"]}
        command = OperationCommand(
            key,
            "wallet_conversion",
            actor_id,
            business_date,
            effective_at,
            tuple(sorted(ids)),
            canonical_json(
                revision_details(
                    {
                        "client": client_id,
                        "source": source_instrument,
                        "target": target_instrument,
                        "quantity": quantity,
                        "fee": fee,
                        "rate": quote.quoted_rate,
                        "physical": physical,
                    },
                    profit_revision_id,
                )
            ),
        )

        async def prepare(work):
            await cast(ClientUnitOfWork, work).clients.lock_client(client_id)

        def build(state):
            original, destination = state.account(source["wallet"]), state.account(target["wallet"])
            principal = -liability_change(-original.native, -original.usdt, -quantity, quote)
            fee_cost = (
                -liability_change(
                    -original.native - quantity, -original.usdt - principal, -fee, quote
                )
                if fee
                else Decimal(0)
            )
            transfer_quote = FxQuote(
                "THB", rounded(principal / quantity, 18), instrument=target_instrument
            )
            target_cost = liability_change(
                -destination.native, -destination.usdt, quantity, transfer_quote
            )
            lines = [
                JournalLine(
                    original.id,
                    "THB",
                    Side.DEBIT,
                    quantity + fee,
                    principal + fee_cost,
                    Valuation.CARRYING,
                ),
                JournalLine(
                    destination.id, "THB", Side.CREDIT, quantity, target_cost, Valuation.CARRYING
                ),
            ]
            if fee_cost:
                lines.append(
                    JournalLine(
                        common["transfer_fee_income"],
                        "USDT",
                        Side.CREDIT,
                        fee_cost,
                        fee_cost,
                        Valuation.BASE,
                    )
                )
            if physical:
                cost = state.position(source_instrument).withdrawal_cost(quantity)
                lines += [
                    JournalLine(
                        source["cash"], "THB", Side.CREDIT, quantity, cost, Valuation.CARRYING
                    ),
                    JournalLine(
                        target["cash"], "THB", Side.DEBIT, quantity, cost, Valuation.CARRYING
                    ),
                ]
            return balance_with_fx(
                tuple(lines),
                income_account_id=source["fx_income"],
                loss_account_id=source["fx_loss"],
            )

        return await self._posting.post(command, build, prepare=prepare,valuation_quotes=(quote,replace(quote,instrument=target_instrument)))
