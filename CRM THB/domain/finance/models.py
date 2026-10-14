from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from enum import StrEnum

from domain.errors import DomainError


class FinanceError(DomainError):
    code = "finance_error"


class InvalidPosting(FinanceError):
    code = "invalid_posting"


class IdempotencyConflict(FinanceError):
    code = "idempotency_conflict"


class PermissionDenied(FinanceError):
    code = "permission_denied"


class PeriodClosed(FinanceError):
    code = "period_closed"


class ReversalBlocked(FinanceError):
    code = "reversal_blocked"


class InsufficientCash(FinanceError):
    code = "insufficient_cash"


def decimal(value: Decimal | str | int) -> Decimal:
    if isinstance(value, float | bool):
        raise InvalidPosting("Денежные значения передаются Decimal или строкой")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        raise InvalidPosting("Некорректное денежное значение") from None
    if not result.is_finite():
        raise InvalidPosting("Денежное значение должно быть конечным")
    return result


def rounded(value: Decimal, precision: int = 8) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        try:
            return decimal(value).quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise InvalidPosting("Сумма выходит за допустимый диапазон") from None


def exact(value: Decimal, precision: int = 8, integer_digits: int = 30) -> Decimal:
    value = decimal(value)
    if value.copy_abs() >= Decimal(10) ** integer_digits or value != rounded(value, precision):
        raise InvalidPosting("Недопустимая точность или величина суммы")
    return value


def canonical_json(value: dict) -> str:
    def encode(item):
        if isinstance(item, Decimal):
            with localcontext() as context:
                context.prec = 80
                return format(decimal(item).normalize(), "f")
        if isinstance(item, datetime) and item.tzinfo is not None:
            return item.astimezone(UTC).isoformat()
        if isinstance(item, date | datetime):
            return item.isoformat()
        raise TypeError(f"Unsupported command value: {type(item).__name__}")

    return json.dumps(
        value,
        default=encode,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


class Side(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"

    @property
    def opposite(self) -> Side:
        return Side.CREDIT if self == Side.DEBIT else Side.DEBIT

    @property
    def sign(self) -> int:
        return 1 if self == Side.DEBIT else -1


class Valuation(StrEnum):
    RATE = "rate"
    CARRYING = "carrying_cost"
    HISTORICAL = "historical"
    BASE = "base"


class PostingMode(StrEnum):
    LEDGER = "ledger"
    MEMORANDUM = "memorandum"


class LedgerContext(StrEnum):
    LEDGER = "ledger"
    INTEREST = "interest"
    EXPENSES = "expenses"
    PROFIT = "profit"


class OperationKind(StrEnum):
    MANUAL = "manual_journal"
    CASH_TRANSFER = "cash_transfer"
    WALLET_DEPOSIT = "wallet_deposit"
    WALLET_WITHDRAWAL = "wallet_withdrawal"
    WALLET_TRANSFER = "wallet_transfer"
    TRADE = "fx_trade"
    WALLET_CONVERSION = "wallet_conversion"
    MARKET_RATE = "market_rate"
    CAPITAL_CONTRIBUTION = "capital_contribution"
    CAPITAL_WITHDRAWAL = "capital_withdrawal"
    CAPITAL_AGREEMENT = "capital_agreement"
    CAPITAL_TERMS = "capital_terms"
    INTEREST_ACCRUAL = "interest_accrual"
    INTEREST_PAYMENT = "interest_payment"
    EXPENSE = "expense"
    EXPENSE_PAYMENT = "expense_payment"
    EXPENSE_NETTING = "expense_netting"
    EXPENSE_CLOSE = "expense_close"
    EXPENSE_REVISION = "expense_revision"
    EXPENSE_RESOLUTION = "expense_resolution"
    EXPENSE_CLOSURE = "expense_closure"
    BEST_CHANGE = "best_change"
    BEST_CHANGE_TRANSFER = "best_change_transfer"
    PROFIT_CLOSURE = "profit_closure"
    PROFIT_PAYMENT = "profit_payment"
    PROFIT_TERMS = "profit_terms"
    PROFIT_REVISION = "profit_revision"
    PROFIT_CONFIGURE = "profit_configure"
    PLATFORM_PAYMENT = "platform_payment"
    REVERSAL = "reversal"


@dataclass(frozen=True, slots=True)
class FxQuote:
    currency: str
    quoted_rate: Decimal
    direction: str = "usdt_per_unit"
    instrument: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "quoted_rate", decimal(self.quoted_rate))
        exact(self.quoted_rate, 18, 20)
        if self.quoted_rate <= 0 or self.direction not in {"usdt_per_unit", "units_per_usdt"}:
            raise InvalidPosting("Курс должен быть положительным, направление — явным")
        if self.currency == "USDT" and self.quoted_rate != 1:
            raise InvalidPosting("Курс USDT равен 1")
        if self.usdt_per_unit <= 0:
            raise InvalidPosting("Курс слишком мал для хранения")

    @property
    def usdt_per_unit(self) -> Decimal:
        with localcontext() as context:
            context.prec = 80
            value = self.quoted_rate if self.direction == "usdt_per_unit" else 1 / self.quoted_rate
            return rounded(value, 18)

    def value(self, quantity: Decimal) -> Decimal:
        with localcontext() as context:
            context.prec = 80
            return rounded(quantity * self.usdt_per_unit)


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str
    precision: int

    def __post_init__(self):
        object.__setattr__(self, "amount", decimal(self.amount))
        if not 0 <= self.precision <= 8:
            raise InvalidPosting("Недопустимая точность валюты")
        exact(self.amount, self.precision)


@dataclass(frozen=True, slots=True)
class JournalLine:
    account_id: int
    currency: str
    side: Side
    native: Decimal
    usdt: Decimal
    valuation: Valuation
    quote: FxQuote | None = None
    rate_id: int | None = None
    unvalued: Decimal = Decimal(0)
    valuation_component: bool = False

    def __post_init__(self):
        object.__setattr__(self, "native", decimal(self.native))
        object.__setattr__(self, "usdt", decimal(self.usdt))
        object.__setattr__(self, 'unvalued', decimal(self.unvalued))
        if not isinstance(self.side, Side) or not isinstance(self.valuation, Valuation):
            raise InvalidPosting("Нужны явные сторона проводки и способ оценки")
        exact(self.native)
        exact(self.usdt)
        exact(self.unvalued)
        if self.unvalued < 0 or self.unvalued > self.native or (self.unvalued and self.currency!='THB'):
            raise InvalidPosting('Некорректная сумма без себестоимости')
        if self.account_id <= 0 or min(self.native, self.usdt) < 0 or self.native + self.usdt <= 0:
            raise InvalidPosting("Некорректная строка проводки")
        if self.valuation == Valuation.RATE and self.quote is None:
            raise InvalidPosting("Для оценки по курсу нужен снимок курса")
        if self.quote is not None and (
            self.quote.currency != self.currency or self.quote.value(self.native) != self.usdt
        ):
            raise InvalidPosting("Сумма строки не соответствует её курсу")
        if self.currency == "USDT" and self.native != self.usdt:
            raise InvalidPosting("Количество USDT должно совпадать с оценкой USDT")


@dataclass(frozen=True, slots=True)
class PostingPlan:
    lines: tuple[JournalLine, ...]
    mode: PostingMode = PostingMode.LEDGER

    def __post_init__(self):
        with localcontext() as context:
            context.prec = 80
            total = sum((line.side.sign * line.usdt for line in self.lines), Decimal(0))
        if (self.mode == PostingMode.LEDGER and (len(self.lines) < 2 or total != 0)) or (
            self.mode == PostingMode.MEMORANDUM and self.lines
        ):
            raise InvalidPosting("Проводки должны балансироваться в USDT")


@dataclass(frozen=True, slots=True)
class AccountState:
    id: int
    currency: str
    instrument: str | None
    precision: int
    normal_side: Side
    purpose: str
    active: bool
    native: Decimal
    usdt: Decimal
    unvalued: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class InventoryPosition:
    instrument: str
    quantity: Decimal
    cost: Decimal
    unvalued: Decimal = Decimal(0)

    def withdrawal_cost(self, quantity: Decimal, *, unvalued: Decimal = Decimal(0)) -> Decimal:
        if quantity <= 0 or quantity > self.quantity:
            raise InsufficientCash("В кассе недостаточно средств")
        priced_quantity=quantity-unvalued
        priced_stock=self.quantity-self.unvalued
        if unvalued<0 or unvalued>min(quantity,self.unvalued) or priced_quantity>priced_stock:
            raise InvalidPosting('Для использования батов без себестоимости нужна оценка операции')
        if priced_quantity==0:
            return Decimal(0)
        if quantity == self.quantity:
            return self.cost
        with localcontext() as context:
            context.prec = 80
            return rounded(self.cost * priced_quantity / priced_stock)


@dataclass(frozen=True, slots=True)
class PostingState:
    accounts: tuple[AccountState, ...]
    positions: tuple[InventoryPosition, ...]

    def account(self, account_id: int) -> AccountState:
        return next(item for item in self.accounts if item.id == account_id)

    def position(self, instrument: str) -> InventoryPosition:
        return next(item for item in self.positions if item.instrument == instrument)


@dataclass(frozen=True, slots=True)
class OperationCommand:
    key: str
    kind: OperationKind
    actor_id: int
    business_date: date
    effective_at: datetime
    accounts: tuple[int, ...]
    details_json: str
    context: LedgerContext = LedgerContext.LEDGER
    mode: PostingMode = PostingMode.LEDGER
    reversal_of: int | None = None
    reason: str | None = None

    def __post_init__(self):
        try:
            object.__setattr__(self, "kind", OperationKind(self.kind))
        except ValueError:
            raise InvalidPosting("Неизвестный тип финансовой операции") from None
        if (
            not self.key.strip()
            or len(self.key) > 200
            or not self.kind.strip()
            or self.actor_id <= 0
        ):
            raise InvalidPosting("Нужны ключ операции, тип и оператор")
        if self.effective_at.tzinfo is None or self.effective_at.utcoffset() is None:
            raise InvalidPosting("Время операции должно содержать часовой пояс")
        if self.reversal_of is not None and not (self.reason or "").strip():
            raise InvalidPosting("Для сторно требуется причина")
        if not isinstance(json.loads(self.details_json), dict):
            raise InvalidPosting("Параметры операции должны быть объектом")
        if self.accounts != tuple(sorted(set(self.accounts))) or any(i <= 0 for i in self.accounts):
            raise InvalidPosting("Счета должны быть уникальны и упорядочены")

    @property
    def fingerprint(self) -> str:
        payload = canonical_json(
            {
                "kind": self.kind,
                "actor": self.actor_id,
                "date": self.business_date,
                "effective_at": self.effective_at,
                "accounts": self.accounts,
                "details": json.loads(self.details_json),
                "context": self.context,
                "mode": self.mode,
                "reversal_of": self.reversal_of,
                "reason": self.reason,
            }
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PostedOperation:
    id: int
    key: str
    fingerprint: str
    kind: str
    context: LedgerContext
    business_date: date
    mode: PostingMode
    reversal_of: int | None


def allocate_percentages(
    total: Decimal, shares: tuple[tuple[int, Decimal], ...], precision: int
) -> tuple[tuple[int, Decimal], ...]:
    exact(total, precision)
    if total < 0 or len({key for key, _ in shares}) != len(shares):
        raise InvalidPosting("Некорректное распределение")
    if any(decimal(p) < 0 or p > 100 or p != rounded(p, 6) for _, p in shares):
        raise InvalidPosting("Некорректные проценты")
    if sum((p for _, p in shares), Decimal(0)) != 100:
        raise InvalidPosting("Сумма долей должна быть 100%")
    return allocate_weights(total, shares, precision)


def allocate_weights(
    total: Decimal, weights: tuple[tuple[int, Decimal], ...], precision: int
) -> tuple[tuple[int, Decimal], ...]:
    """Largest remainders, stable party IDs; preserves all native and USDT units."""
    exact(total, precision)
    if (
        total < 0
        or not 0 <= precision <= 8
        or len({key for key, _ in weights}) != len(weights)
        or any(decimal(weight) < 0 for _, weight in weights)
    ):
        raise InvalidPosting("Некорректное распределение суммы")
    with localcontext() as context:
        context.prec = 80
        denominator = sum((weight for _, weight in weights), Decimal(0))
        if not denominator and total:
            raise InvalidPosting("Для распределения нужны положительные доли")
        quantum = Decimal(1).scaleb(-precision)
        raw = {
            key: total * weight / denominator if denominator else Decimal(0)
            for key, weight in weights
        }
        values = {key: amount.quantize(quantum, rounding=ROUND_DOWN) for key, amount in raw.items()}
        remaining = int((total - sum(values.values())) / quantum)
        order = sorted(raw, key=lambda key: (-(raw[key] - values[key]), key))
        for key in order[:remaining]:
            values[key] += quantum
        return tuple(sorted(values.items()))


def revision_details(details:dict,profit_revision_id:int | None):
    if profit_revision_id is None:
        return details
    if isinstance(profit_revision_id,bool) or not isinstance(profit_revision_id,int) or profit_revision_id<=0:
        raise InvalidPosting('Нужен положительный ID ревизии прибыли')
    return {**details,'profit_revision_id':profit_revision_id}
