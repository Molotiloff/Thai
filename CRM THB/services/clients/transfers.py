"""Transfers between ordinary client wallets; cash stays outside this use case."""

from decimal import localcontext
from typing import cast

from domain.deferred_valuation import transfer_wallet_value
from domain.finance.fx_result import balance_with_fx
from domain.finance.models import (
    InvalidPosting,
    JournalLine,
    OperationCommand,
    Side,
    Valuation,
    canonical_json,
    decimal,
    exact,
    revision_details,
)
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
        quote = await self._queries.internal_quote(instrument,effective_at,key)
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
                        "quoted_rate": quote.quoted_rate if quote else None,
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
            principal,source_pending,target_value,target_pending=transfer_wallet_value(sender,recipient,quantity,quote)
            return balance_with_fx(
                (
                    JournalLine(
                        sender.id,
                        source["currency"],
                        Side.DEBIT,
                        quantity,
                        principal,
                        Valuation.CARRYING,unvalued=abs(source_pending),
                    ),
                    JournalLine(
                        recipient.id,
                        source["currency"],
                        Side.CREDIT,
                        quantity,
                        target_value,
                        Valuation.CARRYING,unvalued=abs(target_pending),
                    ),
                ),
                income_account_id=source["fx_income"],
                loss_account_id=source["fx_loss"],
            )

        return await self._posting.post(command, build, prepare=prepare, work=work,valuation_quotes=(quote,) if quote else ())
