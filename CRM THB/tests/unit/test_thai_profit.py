from decimal import Decimal

import pytest

from domain.accounting_policy import calculate_best_change, payout_fifo
from domain.finance.models import InvalidPosting


@pytest.mark.parametrize(
    "operation,client,fee,dany,business",
    [
        ("sale", "36", "0.85714286", "1", "1"),
        ("purchase", "34", "0.85714286", "1", "1"),
        ("sale", "35", "0", "0", "0"),
        ("sale", "34", "0", "-1.428571", "-1.42857186"),
    ],
)
def test_best_change_formula(operation, client, fee, dany, business):
    result = calculate_best_change(operation, "100", "35", client)
    assert result.fee_usdt == Decimal(fee)
    assert result.daniyar_usdt == Decimal(dany)
    assert result.business_usdt == Decimal(business)
    assert result.gross_usdt == result.fee_usdt + result.daniyar_usdt + result.business_usdt


@pytest.mark.parametrize(
    "quantity,market,client",
    [("0", "35", "36"), ("100", "0", "36"), ("100", "35", "-1"), ("1.0000001", "35", "36")],
)
def test_invalid_best_change(quantity, market, client):
    with pytest.raises(InvalidPosting):
        calculate_best_change("sale", quantity, market, client)


def test_payment_fifo():
    rows = [
        {"id": 1, "operation_id": 10, "remaining_usdt": Decimal(2)},
        {"id": 2, "operation_id": 20, "remaining_usdt": Decimal(5)},
    ]
    assert payout_fifo(rows, Decimal(4)) == ((1, 10, Decimal(2)), (2, 20, Decimal(2)))
    with pytest.raises(InvalidPosting):
        payout_fifo(rows, Decimal(8))
