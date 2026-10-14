from decimal import Decimal

import pytest

from domain.capital import CapitalLot, fifo, interest_amount, monthly_percent
from domain.finance.models import InvalidPosting


@pytest.mark.parametrize('value', ['-1', '100.000001', '1.0000001', 'NaN'])
def test_interest_percent_rejects_invalid_values(value):
    with pytest.raises(InvalidPosting):
        monthly_percent(Decimal(value))


def test_monthly_interest_is_percent_of_usdt_capital():
    assert interest_amount(Decimal(10000), Decimal(2)) == 200
    assert interest_amount(Decimal(10000), Decimal(0)) == 0


def test_fifo_partial_and_final_principal_preserve_total():
    lots = (CapitalLot(1, 1, Decimal(35000), Decimal(1000)), CapitalLot(2, 2, Decimal(18000), Decimal(500)))
    allocation = fifo(lots, Decimal(40000))
    assert [(a.lot_id, a.quantity, a.principal) for a in allocation] == [(1, 35000, 1000), (2, 5000, Decimal('138.88888889'))]
    final = fifo((CapitalLot(2, 2, Decimal(13000), Decimal('361.11111111')),), Decimal(13000))
    assert final[0].principal+allocation[1].principal == 500
    with pytest.raises(InvalidPosting):
        fifo(lots, Decimal(53001))
