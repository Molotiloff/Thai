"""Transfers between ordinary client wallets; cash stays outside this use case."""

from decimal import localcontext
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
from services.clients.ports import ClientUnitOfWork, WalletReadPort
from services.finance.posting import OperationPostingService


class ClientWalletTransferService:
    def __init__(self, posting: OperationPostingService, queries: WalletReadPort):
        self._posting, self._queries = posting, queries

    async def transfer(
        self,
        *,
        key,
        actor_id,
        source_client_id,
        target_client_id,
        instrument,
        quantity,
        business_date,
        effective_at,
        quoted_rate=None,
        comment="",
        profit_revision_id=None,
        work=None,
        transfer_root_id=None,
    ):
        if source_client_id == target_client_id:
            raise InvalidPosting("Отправитель и получатель должны быть разными клиентами")
        source = await self._queries.wallet_accounts(source_client_id, instrument)
        target = await self._queries.wallet_accounts(target_client_id, instrument)
        quantity = exact(decimal(quantity), source["precision"])
        if quantity <= 0:
            raise InvalidPosting("Сумма перевода должна быть положительной")
        if source["currency"] != "USDT" and quoted_rate is None:
            raise InvalidPosting("Для перевода валюты нужен курс относительно USDT")
        quote = FxQuote(
            source["currency"],
            decimal(quoted_rate if quoted_rate is not None else 1),
            "units_per_usdt" if source["currency"] == "THB" else "usdt_per_unit",
            instrument,
        )
        command = OperationCommand(
            key,
            "wallet_transfer",
            actor_id,
            business_date,
            effective_at,
            tuple(
                sorted({source["wallet"], target["wallet"], source["fx_income"], source["fx_loss"]})
            ),
            canonical_json(
                revision_details(
                    {
                        "source_client": source_client_id,
                        "target_client": target_client_id,
                        "instrument": instrument,
                        "quantity": quantity,
                        "quoted_rate": quote.quoted_rate,
                        "comment": comment,
                        **({"transfer_root_id": transfer_root_id} if transfer_root_id is not None else {}),
                    },
                    profit_revision_id,
                )
            ),
        )

        async def prepare(work):
            for client in sorted((source_client_id, target_client_id)):
                await cast(ClientUnitOfWork, work).clients.lock_client(client)

        def build(state):
            with localcontext() as context:
                context.prec = 80
                return build_lines(state)

        def build_lines(state):
            sender, recipient = state.account(source["wallet"]), state.account(target["wallet"])
            principal = -liability_change(-sender.native, -sender.usdt, -quantity, quote)
            carrying_quote = FxQuote(
                source["currency"], rounded(principal / quantity, 18), instrument=instrument
            )
            target_value = liability_change(
                -recipient.native, -recipient.usdt, quantity, carrying_quote
            )
            return balance_with_fx(
                (
                    JournalLine(
                        sender.id,
                        source["currency"],
                        Side.DEBIT,
                        quantity,
                        principal,
                        Valuation.CARRYING,
                    ),
                    JournalLine(
                        recipient.id,
                        source["currency"],
                        Side.CREDIT,
                        quantity,
                        target_value,
                        Valuation.CARRYING,
                    ),
                ),
                income_account_id=source["fx_income"],
                loss_account_id=source["fx_loss"],
            )

        return await self._posting.post(command, build, prepare=prepare, work=work,valuation_quotes=(quote,))
