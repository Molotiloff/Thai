from decimal import Decimal

import pytest

from domain.finance.fx_result import balance_with_fx
from domain.finance.models import FxQuote, InvalidPosting, JournalLine, Side, Valuation
from domain.wallet_valuation import liability_change
from services.loans import debt_direction


def test_partial_wallet_return_uses_historical_cost():
    assert liability_change(Decimal(100000), Decimal('2857.14285714'), Decimal(-30000), None) == Decimal('-857.14285714')


def test_zero_crossing_clears_historical_cost_and_values_only_new_debt():
    quote = FxQuote('THB', Decimal(36), 'units_per_usdt')
    assert liability_change(Decimal(-3500), Decimal(-100), Decimal(3500), quote) == 100
    assert liability_change(Decimal(-3500), Decimal(-100), Decimal(7100), quote) == 200
    with pytest.raises(InvalidPosting):
        liability_change(Decimal(0), Decimal(0), Decimal(-3500), None)


@pytest.mark.parametrize(('value','direction'), [(1,'exchange_owes_client'),(-1,'client_owes_exchange'),(0,'settled')])
def test_signed_loan_direction(value, direction):
    assert debt_direction(Decimal(value)) == direction


def test_fx_loss_preserves_large_usdt_fraction_without_ambient_decimal_context():
    amount = Decimal('12345678901234567890123456789.12345678')
    plan = balance_with_fx((JournalLine(1,'USDT',Side.CREDIT,amount,amount,Valuation.BASE),),
        income_account_id=2, loss_account_id=3)
    assert plan.lines[-1].usdt == amount
