"""Capital calculations independent of persistence, transports and client wallets."""
from dataclasses import dataclass
from decimal import Decimal, localcontext

from domain.finance.models import InvalidPosting, decimal, exact, rounded


def monthly_percent(value: Decimal) -> Decimal:
    value = decimal(value)
    exact(value, 6, 3)
    if not 0 <= value <= 100:
        raise InvalidPosting("Процент должен быть от 0 до 100; 2 означает 2%")
    return value


def interest_amount(basis: Decimal, percent: Decimal) -> Decimal:
    if basis < 0:
        raise InvalidPosting("База капитала не может быть отрицательной")
    with localcontext() as ctx:
        ctx.prec = 80
        return rounded(basis * monthly_percent(percent) / 100)


@dataclass(frozen=True)
class CapitalLot:
    id: int
    operation_id: int
    quantity: Decimal
    principal: Decimal


@dataclass(frozen=True)
class LotAllocation:
    lot_id: int
    source_operation_id: int
    quantity: Decimal
    principal: Decimal


def fifo(lots: tuple[CapitalLot, ...], quantity: Decimal) -> tuple[LotAllocation, ...]:
    if quantity <= 0:
        raise InvalidPosting("Сумма возврата должна быть положительной")
    with localcontext() as ctx:
        ctx.prec = 80
        remaining = quantity
        allocations = []
        for lot in lots:
            take = min(remaining, lot.quantity)
            if take <= 0:
                continue
            cost = lot.principal if take == lot.quantity else rounded(lot.principal * take / lot.quantity)
            if cost <= 0:
                raise InvalidPosting("Возвращаемая часть капитала меньше точности USDT")
            allocations.append(LotAllocation(lot.id, lot.operation_id, take, cost))
            remaining -= take
            if remaining == 0:
                return tuple(allocations)
        raise InvalidPosting("Сумма возврата превышает доступный капитал в исходной валюте")
