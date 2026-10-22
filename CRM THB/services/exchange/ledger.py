from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from db_asyncpg.ports.ledger import TransactionRepositoryPort
from domain.finance.models import InvalidPosting
from services.act_counter import AppliedExchangeMovement
from services.clients.ports import ClientUnitOfWork


class LedgerAction(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"


@dataclass(frozen=True, slots=True)
class ExchangeLedgerCommand:
    currency_code: str
    amount: Decimal
    action: LedgerAction
    comment: str
    source: str
    idempotency_key: str
    reported_direction: str


async def apply_ledger_commands(
    repository: TransactionRepositoryPort,
    *,
    client_id: int,
    commands: Sequence[ExchangeLedgerCommand],
) -> list[AppliedExchangeMovement]:
    movements: list[AppliedExchangeMovement] = []
    for command in commands:
        operation = (
            repository.deposit if command.action is LedgerAction.DEPOSIT else repository.withdraw
        )
        transaction_id = await operation(
            client_id=client_id,
            currency_code=command.currency_code,
            amount=command.amount,
            comment=command.comment,
            source=command.source,
            idempotency_key=command.idempotency_key,
        )
        movements.append(
            AppliedExchangeMovement(
                transaction_id=int(transaction_id),
                currency_code=command.currency_code,
                direction=command.reported_direction,
                amount=command.amount,
            )
        )
    return movements


def plan_create_ledger(
    *,
    receive_code: str,
    receive_amount: Decimal,
    receive_comment: str,
    pay_code: str,
    pay_amount: Decimal,
    pay_comment: str,
    receive_is_deposit: bool,
    pay_is_withdraw: bool,
    receive_idempotency_key: str,
    pay_idempotency_key: str,
    tracked_currency_codes: Collection[str] | None,
) -> tuple[ExchangeLedgerCommand, ...]:
    commands: list[ExchangeLedgerCommand] = []
    if is_tracked(receive_code, tracked_currency_codes):
        commands.append(
            _command(
                receive_code,
                receive_amount,
                _normal_receive_action(receive_is_deposit),
                receive_comment,
                "exchange",
                receive_idempotency_key,
                "IN",
            )
        )
    if is_tracked(pay_code, tracked_currency_codes):
        commands.append(
            _command(
                pay_code,
                pay_amount,
                _normal_pay_action(pay_is_withdraw),
                pay_comment,
                "exchange",
                pay_idempotency_key,
                "OUT",
            )
        )
    return tuple(commands)


def plan_cancel_ledger(
    *,
    request_id: str,
    chat_id: int,
    message_id: int,
    receive_code: str,
    receive_amount: Decimal,
    pay_code: str,
    pay_amount: Decimal,
    receive_is_deposit: bool,
    pay_is_withdraw: bool,
    tracked_currency_codes: Collection[str] | None,
) -> tuple[tuple[ExchangeLedgerCommand, ...], str | None, str | None]:
    commands: list[ExchangeLedgerCommand] = []
    receive_sign: str | None = None
    pay_sign: str | None = None
    if is_tracked(receive_code, tracked_currency_codes):
        receive_sign = "-" if receive_is_deposit else "+"
        commands.append(
            _command(
                receive_code,
                receive_amount,
                _reverse_receive_action(receive_is_deposit),
                f"cancel req {request_id}",
                "exchange_cancel",
                f"cancel:{chat_id}:{message_id}:recv",
                "OUT" if receive_is_deposit else "IN",
            )
        )
    if is_tracked(pay_code, tracked_currency_codes):
        pay_sign = "+" if pay_is_withdraw else "-"
        commands.append(
            _command(
                pay_code,
                pay_amount,
                _reverse_pay_action(pay_is_withdraw),
                f"cancel req {request_id}",
                "exchange_cancel",
                f"cancel:{chat_id}:{message_id}:pay",
                "IN" if pay_is_withdraw else "OUT",
            )
        )
    return tuple(commands), receive_sign, pay_sign


def plan_edit_ledger(
    *,
    old_receive_code: str,
    old_receive_amount: Decimal,
    old_pay_code: str,
    old_pay_amount: Decimal,
    receive_code: str,
    receive_amount: Decimal,
    pay_code: str,
    pay_amount: Decimal,
    receive_is_deposit: bool,
    pay_is_withdraw: bool,
    idempotency_prefix: str,
    tracked_currency_codes: Collection[str] | None,
) -> tuple[ExchangeLedgerCommand, ...]:
    commands: list[ExchangeLedgerCommand] = []
    currencies_changed = old_receive_code != receive_code or old_pay_code != pay_code
    if currencies_changed:
        _append_if_tracked(
            commands,
            old_receive_code,
            tracked_currency_codes,
            old_receive_amount,
            _reverse_receive_action(receive_is_deposit),
            "edit revert old recv",
            f"{idempotency_prefix}:revert:recv",
            "OUT",
        )
        _append_if_tracked(
            commands,
            old_pay_code,
            tracked_currency_codes,
            old_pay_amount,
            _reverse_pay_action(pay_is_withdraw),
            "edit revert old pay",
            f"{idempotency_prefix}:revert:pay",
            "IN",
        )
        _append_if_tracked(
            commands,
            receive_code,
            tracked_currency_codes,
            receive_amount,
            _normal_receive_action(receive_is_deposit),
            "edit recv apply",
            f"{idempotency_prefix}:apply:recv",
            "IN",
        )
        _append_if_tracked(
            commands,
            pay_code,
            tracked_currency_codes,
            pay_amount,
            _normal_pay_action(pay_is_withdraw),
            "edit pay apply",
            f"{idempotency_prefix}:apply:pay",
            "OUT",
        )
        return tuple(commands)

    _append_delta(
        commands,
        code=receive_code,
        delta=receive_amount - old_receive_amount,
        normal_action=_normal_receive_action(receive_is_deposit),
        reverse_action=_reverse_receive_action(receive_is_deposit),
        leg="recv",
        positive_direction="IN",
        negative_direction="OUT",
        idempotency_prefix=idempotency_prefix,
        tracked_currency_codes=tracked_currency_codes,
    )
    _append_delta(
        commands,
        code=pay_code,
        delta=pay_amount - old_pay_amount,
        normal_action=_normal_pay_action(pay_is_withdraw),
        reverse_action=_reverse_pay_action(pay_is_withdraw),
        leg="pay",
        positive_direction="OUT",
        negative_direction="IN",
        idempotency_prefix=idempotency_prefix,
        tracked_currency_codes=tracked_currency_codes,
    )
    return tuple(commands)


def is_tracked(code: str, tracked_currency_codes: Collection[str] | None) -> bool:
    if tracked_currency_codes is None:
        return True
    normalized = {str(item).upper() for item in tracked_currency_codes}
    return str(code).upper() in normalized


def _append_delta(
    commands: list[ExchangeLedgerCommand],
    *,
    code: str,
    delta: Decimal,
    normal_action: LedgerAction,
    reverse_action: LedgerAction,
    leg: str,
    positive_direction: str,
    negative_direction: str,
    idempotency_prefix: str,
    tracked_currency_codes: Collection[str] | None,
) -> None:
    if delta == 0 or not is_tracked(code, tracked_currency_codes):
        return
    positive = delta > 0
    suffix = "delta+" if positive else "delta-"
    commands.append(
        _command(
            code,
            delta if positive else -delta,
            normal_action if positive else reverse_action,
            f"edit {leg} {suffix}",
            "exchange_edit",
            f"{idempotency_prefix}:{suffix}:{leg}",
            positive_direction if positive else negative_direction,
        )
    )


def _append_if_tracked(
    commands: list[ExchangeLedgerCommand],
    code: str,
    tracked_currency_codes: Collection[str] | None,
    amount: Decimal,
    action: LedgerAction,
    comment: str,
    idempotency_key: str,
    reported_direction: str,
) -> None:
    if is_tracked(code, tracked_currency_codes):
        commands.append(
            _command(
                code,
                amount,
                action,
                comment,
                "exchange_edit",
                idempotency_key,
                reported_direction,
            )
        )


def _command(
    code: str,
    amount: Decimal,
    action: LedgerAction,
    comment: str,
    source: str,
    idempotency_key: str,
    reported_direction: str,
) -> ExchangeLedgerCommand:
    return ExchangeLedgerCommand(
        currency_code=code,
        amount=amount,
        action=action,
        comment=comment,
        source=source,
        idempotency_key=idempotency_key,
        reported_direction=reported_direction,
    )


def _normal_receive_action(receive_is_deposit: bool) -> LedgerAction:
    return LedgerAction.DEPOSIT if receive_is_deposit else LedgerAction.WITHDRAW


def _reverse_receive_action(receive_is_deposit: bool) -> LedgerAction:
    return LedgerAction.WITHDRAW if receive_is_deposit else LedgerAction.DEPOSIT


def _normal_pay_action(pay_is_withdraw: bool) -> LedgerAction:
    return LedgerAction.WITHDRAW if pay_is_withdraw else LedgerAction.DEPOSIT


def _reverse_pay_action(pay_is_withdraw: bool) -> LedgerAction:
    return LedgerAction.DEPOSIT if pay_is_withdraw else LedgerAction.WITHDRAW


class MissingMarketQuote(InvalidPosting):
    pass


class TradingPort(Protocol):
    async def start_payment_watch(self, *, actor_id,client_id,chat_id,reply_message_id,address,
                                  our_address,quantity,test_mode,timeout_at) -> dict: ...
    async def lock_payment_watch(self, watch_id: int) -> dict: ...
    async def payment_watch_changed(self, watch_id: int) -> None: ...
    async def change_payment_watch(self, watch_id: int, action: str, timeout_at) -> None: ...
    async def confirmed_payment(self, watch_id: int,operation_id: int,transfer,phase: str,review: bool) -> None: ...
    async def payment_hash_exists(self, tx_hash: str) -> bool: ...
    async def set_payment_wallet(self, address: str) -> None: ...

    async def cash_confirmations(self, deal_id: int) -> list[dict]: ...
    async def confirm_cash_exchange(self, deal_id, direction, instrument, quantity, quoted_rate, actor_id, confirmed_at) -> None: ...
    async def exchange_current(self, root_id: int) -> dict: ...
    async def create(
        self, actor_id: int, client_id, attributes: dict, external_reference: str, kind: str = "fx_trade"
    ) -> dict: ...
    async def lock(self, deal_id: int) -> dict: ...
    async def replace_cash_request(self, deal_id: int, attributes: dict, expected_revision: int) -> None: ...
    async def cash_request_changed(self, deal_id: int) -> None: ...
    async def complete_cash_request(self, deal_id: int, operation_id: int) -> None: ...
    async def ready(self, deal_id: int) -> dict: ...
    async def cancel_draft(self, deal_id: int, reason: str) -> dict: ...
    async def post(self, deal_id: int, operation_id: int, attributes: dict) -> None: ...
    async def quote(self, instrument: str, effective_at): ...
    async def cancelled(self, deal_id, operation_id, reason, replacement) -> None: ...
    async def watch(self, deal_id, actor_id, chat_id, address, our_address, timeout_at) -> dict: ...
    async def settlement(self, deal_id, operation_id, fact: dict) -> None: ...
    async def guard_watch(self, deal_id, fact: dict | None) -> None: ...
    async def reversible(self, deal_id: int) -> None: ...


class TradingUnitOfWork(ClientUnitOfWork, Protocol):
    trades: TradingPort


class TradingReadPort(Protocol):
    async def payment_watch(self, watch_id: int) -> dict: ...
    async def payment_watches(self) -> list[dict]: ...
    async def payment_wallet(self, fallback: str | None = None) -> str | None: ...

    async def client_by_chat(self, chat_id: int) -> int | None: ...
    async def accounts(self, client_id, receive: str, pay: str, mode: str) -> dict: ...
    async def detail(self, deal_id: int) -> dict: ...
    async def internal_rates(self, *, at=None) -> list[dict]: ...
    async def exchange_context(self, deal_id: int) -> dict: ...
    async def exchange_by_message(self, chat_id: int, message_id: int) -> int | None: ...
    async def exchange_by_reference(self, reference: str) -> dict | None: ...

