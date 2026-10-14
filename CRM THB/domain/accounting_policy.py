from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum

from domain.finance.models import FxQuote, InvalidPosting, decimal, exact, rounded

from .errors import DomainValidationError
from .value_objects import CurrencyCode, DealStatus

QUANTITY_QUANTUM = Decimal("0.00000001")
RATE_QUANTUM = Decimal("0.000001")
RUB_QUANTUM = Decimal("0.01")


def is_financially_posted(status: DealStatus | str) -> bool:
    normalized = status if isinstance(status, DealStatus) else DealStatus(status)
    return normalized is DealStatus.DONE


def quantize_quantity(value: Decimal) -> Decimal:
    return value.quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)


def quantize_rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def quantize_rub(value: Decimal) -> Decimal:
    return value.quantize(RUB_QUANTUM, rounding=ROUND_HALF_UP)


class ClientRubValuationPolicy(StrEnum):
    STORED_RUB_ONLY = "stored_rub_only"


@dataclass(frozen=True, slots=True)
class RubValuation:
    amount: Decimal | None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationAmounts:
    total_rub: Decimal
    total_balances: Decimal
    fact_turnover: Decimal
    accumulated_profit: Decimal
    turnover: Decimal
    gap: Decimal
    fact_rub: Decimal


def calculate_reconciliation(
    *,
    rub_cash: Decimal,
    rub_in_currency: Decimal,
    client_balances: Decimal,
    skyex_balances: Decimal,
    invested_capital: Decimal,
    income: Decimal,
    expenses: Decimal,
) -> ReconciliationAmounts:
    total_rub = rub_cash + rub_in_currency
    total_balances = client_balances + skyex_balances
    fact_turnover = total_rub - total_balances
    accumulated_profit = income - expenses
    turnover = invested_capital + accumulated_profit
    return ReconciliationAmounts(
        total_rub=total_rub,
        total_balances=total_balances,
        fact_turnover=fact_turnover,
        accumulated_profit=accumulated_profit,
        turnover=turnover,
        gap=fact_turnover - turnover,
        fact_rub=total_rub + total_balances,
    )


def value_client_balance_in_rub(
    *,
    amount: Decimal,
    currency: CurrencyCode | str,
    policy: ClientRubValuationPolicy = ClientRubValuationPolicy.STORED_RUB_ONLY,
) -> RubValuation:
    normalized = currency if isinstance(currency, CurrencyCode) else CurrencyCode(currency)
    if policy is not ClientRubValuationPolicy.STORED_RUB_ONLY:
        raise DomainValidationError(f"Unsupported client RUB valuation policy: {policy}")
    if normalized == CurrencyCode("RUB"):
        return RubValuation(amount=amount)
    return RubValuation(
        amount=None,
        warning=f"RUB valuation is not configured for {normalized}",
    )


@dataclass(frozen=True, slots=True)
class BestChangeResult:
    operation: str
    quantity: Decimal
    market_rate: Decimal
    client_rate: Decimal
    thb_quantity: Decimal
    gross_thb: Decimal
    gross_usdt: Decimal
    fee_usdt: Decimal
    pool_usdt: Decimal
    daniyar_usdt: Decimal
    business_usdt: Decimal


def calculate_best_change(operation, quantity, market_rate, client_rate):
    if operation not in ("sale", "purchase"):
        raise InvalidPosting("BestChange: sale или purchase")
    quantity = exact(decimal(quantity), 6)
    market_rate, client_rate = decimal(market_rate), decimal(client_rate)
    quote = FxQuote("THB", market_rate, "units_per_usdt")
    FxQuote("THB", client_rate, "units_per_usdt")
    if quantity <= 0:
        raise InvalidPosting("Количество USDT должно быть положительным")
    with localcontext() as context:
        context.prec = 80
        thb_quantity = exact(rounded(quantity * client_rate, 2), 2)
        gross_thb = (thb_quantity - quantity * market_rate) * (1 if operation == "sale" else -1)
        gross = quote.value(gross_thb)
        fee = rounded(gross * Decimal(".30")) if gross > 0 else Decimal(0)
        pool = gross - fee
        # Client USDT wallets use six decimals; the business keeps the exact residual.
        daniyar = rounded(pool / 2, 6)
        return BestChangeResult(
            operation,
            quantity,
            market_rate,
            client_rate,
            thb_quantity,
            gross_thb,
            gross,
            fee,
            pool,
            daniyar,
            pool - daniyar,
        )


def payout_fifo(rows, amount):
    with localcontext() as context:
        context.prec = 80
        remaining, allocations = amount, []
        for row in rows:
            take = min(remaining, row["remaining_usdt"])
            if take > 0:
                allocations.append((row["id"], row["operation_id"], take))
                remaining -= take
            if not remaining:
                break
        if remaining or amount <= 0:
            raise InvalidPosting("Выплата превышает начисленное непогашенное обязательство")
        return tuple(allocations)


def summarize(sources):
    with localcontext() as context:
        context.prec = 80
        income = sum(
            (
                row["credit_signed_usdt"]
                for row in sources
                if row["purpose"] not in ("operating_expense", "investor_interest_expense")
            ),
            Decimal(0),
        )
        expenses = -sum(
            (row["credit_signed_usdt"] for row in sources if row["purpose"] == "operating_expense"),
            Decimal(0),
        )
        interest = -sum(
            (
                row["credit_signed_usdt"]
                for row in sources
                if row["purpose"] == "investor_interest_expense"
            ),
            Decimal(0),
        )
        return {
            "income_usdt": income,
            "business_expenses_usdt": expenses,
            "investor_interest_usdt": interest,
            "net_profit_usdt": income - expenses - interest,
        }

