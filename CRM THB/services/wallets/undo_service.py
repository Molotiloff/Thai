from __future__ import annotations

from decimal import Decimal, InvalidOperation

from db_asyncpg.ports.workflows import ClientWalletTransactionRepositoryPort
from domain.finance.models import InvalidPosting
from services.clients.ports import WalletAuditReadPort
from services.finance.ports import FinanceUowFactory
from services.finance.posting import ReversalService
from services.metadata_commands import MetadataCommandService
from services.wallets.command_parser import WalletCommandParser
from services.wallets.models import WalletCommandResult
from services.wallets.text_builder import WalletTextBuilder


class WalletUndoService:
    def __init__(
        self,
        *,
        repo: ClientWalletTransactionRepositoryPort | None = None,
        accounting_queries: WalletAuditReadPort | None = None,
        reversals: ReversalService | None = None, uow_factory: FinanceUowFactory | None = None,
        parser: WalletCommandParser | None = None,
        text_builder: WalletTextBuilder | None = None,
    ) -> None:
        self.repo = repo
        self._accounting, self._reversals = accounting_queries, reversals
        self._commands = MetadataCommandService(uow_factory) if uow_factory else None
        self.parser = parser or WalletCommandParser()
        self.text_builder = text_builder or WalletTextBuilder()

    async def undo_operation(
        self,
        *,
        chat_id: int,
        chat_name: str,
        message_id: int,
        code_raw: str,
        sign: str,
        amt_str: str,
    ) -> WalletCommandResult:
        code = self.parser.normalize_code_alias(code_raw)
        try:
            amount = Decimal(amt_str)
        except InvalidOperation:
            return WalletCommandResult(ok=False, message_text="Ошибка суммы")

        client_id = await self.repo.ensure_client(chat_id, chat_name)
        idempotency_key = f"undo:{chat_id}:{message_id}"
        existing = await self.repo.get_transaction_by_idempotency_key(
            client_id=client_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return await self._already_undone(client_id=client_id, code=code)

        if sign == "+":
            await self.repo.withdraw(
                client_id=client_id,
                currency_code=code,
                amount=amount,
                comment="undo",
                source="undo",
                idempotency_key=idempotency_key,
            )
            applied_sign = "-"
        elif sign == "-":
            await self.repo.deposit(
                client_id=client_id,
                currency_code=code,
                amount=amount,
                comment="undo",
                source="undo",
                idempotency_key=idempotency_key,
            )
            applied_sign = "+"
        else:
            return WalletCommandResult(ok=False, message_text="Некорректный знак")

        rows = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in rows if str(r["currency_code"]).upper() == code), None)
        if acc:
            precision = int(acc["precision"])
            cur_bal = Decimal(str(acc["balance"]))
            return WalletCommandResult(
                ok=True,
                message_text=self.text_builder.undo_success(
                    code=code,
                    amount=amount,
                    precision=precision,
                    applied_sign=applied_sign,
                    balance=cur_bal,
                ),
            )

        return WalletCommandResult(ok=True, message_text=f"Счёт {code} не найден.")

    async def _already_undone(self, *, client_id: int, code: str) -> WalletCommandResult:
        rows = await self.repo.snapshot_wallet(client_id)
        account = next(
            (row for row in rows if str(row["currency_code"]).upper() == code),
            None,
        )
        if account is None:
            return WalletCommandResult(
                ok=False,
                message_text=f"Операция уже отменена\nСчёт {code} не найден.",
            )
        return WalletCommandResult(
            ok=False,
            message_text=self.text_builder.undo_already_done_with_balance(
                code=code,
                balance=Decimal(str(account["balance"])),
                precision=int(account["precision"]),
            ),
        )


    async def undo_accounting(self, *, chat_id, message_id, actor_id, business_date, effective_at):
        operation_id = await self._accounting.wallet_operation_by_message(chat_id,message_id)
        if operation_id is None:
            raise InvalidPosting('Оригинальная операция корректировки не найдена')
        async def action(work):
            original = await work.journal.get(operation_id)
            # The lookup only accepts a persisted message link, never an amount supplied by a button.
            # Read the operation's owner through the same repository/transaction.
            details = await work.clients.wallet_adjustment_details(original.id)
            client_id = details['client']
            await work.journal.lock_posting_period()
            await work.clients.lock_client(client_id)
            previous = await work.journal.find_by_key(f'wallet-undo:{operation_id}')
            if previous is None:
                previous = await self._reversals.reverse(original_id=operation_id,key=f'wallet-undo:{operation_id}',
                    actor_id=actor_id,business_date=business_date,effective_at=effective_at,
                    reason='Отмена корректировки кошелька в Telegram',work=work)
            return {'operation_id':previous.id,'original_id':operation_id,'client_id':client_id,
                    'instrument':details['instrument']}
        return await self._commands.execute(key=f'wallet-undo-command:{operation_id}',actor_id=actor_id,
            kind='wallet_undo',permission='finance.reverse',payload={'operation_id':operation_id},action=action)
