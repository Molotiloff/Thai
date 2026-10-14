"""Exact share allocation and FIFO settlement, independent of storage and UI."""

from dataclasses import dataclass
from decimal import Decimal, localcontext

from domain.finance.models import InvalidPosting, allocate_weights, decimal, exact, rounded


@dataclass(frozen=True, slots=True)
class ExpenseShare:
    code: str
    party_id: int
    percent: Decimal
    amount: Decimal
    usdt: Decimal


def allocate_shares(amount, amount_usdt, precision, inputs, participants):
    if set(inputs) - {"daniyar", "pe", "skyex", "common"}:
        raise InvalidPosting("Неизвестная доля расхода")
    values = {
        code: exact(decimal(inputs.get(code, "0")), 6)
        for code in ("daniyar", "pe", "skyex", "common")
    }
    if any(value < 0 or value > 100 for value in values.values()) or sum(values.values()) != 100:
        raise InvalidPosting("Доли расхода должны составлять ровно 100%")
    expanded = {
        code: values[code] + (values["common"] / 2 if code in ("pe", "skyex") else 0)
        for code in ("daniyar", "pe", "skyex")
    }
    # Splitting an odd millionth of a percent needs one extra decimal place.
    weights = {participants[code]: percent for code, percent in expanded.items()}
    native = dict(allocate_weights(amount, tuple(weights.items()), precision))
    costs = dict(allocate_weights(amount_usdt, tuple(native.items()), 8))
    return tuple(
        ExpenseShare(
            code, participants[code], percent, native[participants[code]], costs[participants[code]]
        )
        for code, percent in expanded.items()
    ), values


@dataclass(frozen=True, slots=True)
class DebtAllocation:
    obligation_id: int
    amount: Decimal
    usdt: Decimal
    account_id: int | None
    source_operation_id: int
    direction: str = "forward"


def settle_fifo(obligations, amount, direction="forward"):
    with localcontext() as context:
        context.prec = 80
        remaining = amount
        allocations = []
        for row in obligations:
            available = row["remaining"]
            if available <= 0 or remaining <= 0:
                continue
            taken = min(available, remaining)
            cost = (
                row["remaining_usdt"]
                if taken == available
                else rounded(row["remaining_usdt"] * taken / available)
            )
            allocations.append(
                DebtAllocation(
                    row["id"],
                    taken,
                    cost,
                    row["business_ledger_account_id"],
                    row["source_operation_id"],
                    direction,
                )
            )
            remaining -= taken
        if remaining:
            raise InvalidPosting("Сумма расчёта превышает непогашенные обязательства")
        return tuple(allocations)
