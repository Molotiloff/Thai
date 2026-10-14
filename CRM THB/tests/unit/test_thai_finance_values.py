from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from api.thai_finance import business_day
from domain.finance.models import (
    FxQuote,
    InvalidPosting,
    InventoryPosition,
    JournalLine,
    Money,
    OperationCommand,
    PostingMode,
    PostingPlan,
    Side,
    Valuation,
    allocate_percentages,
    canonical_json,
    decimal,
)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", True, 0.1, "broken"])
def test_invalid_money_is_rejected(value):
    with pytest.raises(InvalidPosting):
        decimal(value)


def test_money_rejects_extra_precision_instead_of_rounding_input():
    with pytest.raises(InvalidPosting):
        Money(Decimal("1.001"), "THB", 2)
    assert Money(Decimal("1.00"), "THB", 2).amount == 1


def test_inverse_rate_and_usdt_valuation():
    quote = FxQuote("THB", Decimal(35), "units_per_usdt")
    assert quote.usdt_per_unit == Decimal("0.028571428571428571")
    assert quote.value(Decimal(35000)) == 1000


@pytest.mark.parametrize("rate", ["0", "-1", "NaN", "100000000000000000000"])
def test_invalid_rate_is_rejected(rate):
    with pytest.raises(InvalidPosting):
        FxQuote("THB", Decimal(rate))


def test_usdt_rate_is_one():
    with pytest.raises(InvalidPosting):
        FxQuote("USDT", Decimal(2))


def test_final_cash_withdrawal_preserves_exact_cost():
    position = InventoryPosition("THB_CASH", Decimal(53000), Decimal(1500))
    assert position.withdrawal_cost(Decimal(40000)) == Decimal("1132.07547170")
    assert position.withdrawal_cost(Decimal(53000)) == 1500


def test_percentage_remainder_has_stable_recipient_and_preserves_total():
    shares = ((3, Decimal("33.333334")), (2, Decimal("33.333333")), (1, Decimal("33.333333")))
    result = allocate_percentages(Decimal("0.02"), shares, 2)
    assert result == ((1, Decimal("0.01")), (2, Decimal("0.00")), (3, Decimal("0.01")))
    assert allocate_percentages(Decimal("0.02"), shares[::-1], 2) == result


def test_base_balance_is_required_and_memorandum_has_no_lines():
    debit = JournalLine(1, "USDT", Side.DEBIT, Decimal(1), Decimal(1), Valuation.BASE)
    with pytest.raises(InvalidPosting):
        PostingPlan((debit,))
    with pytest.raises(InvalidPosting):
        PostingPlan((debit,), PostingMode.MEMORANDUM)
    assert PostingPlan((), PostingMode.MEMORANDUM).lines == ()


def test_canonical_amount_does_not_lose_large_decimal_digits():
    amount = Decimal("123456789012345678901234567890.12345678")
    assert canonical_json({"amount": amount}) == '{"amount":"123456789012345678901234567890.12345678"}'


def test_bangkok_month_boundary_uses_business_timezone():
    instant = datetime(2026, 9, 30, 20, tzinfo=UTC)
    assert business_day(instant, None) == date(2026, 10, 1)


def test_changed_command_has_different_hash_and_equivalent_decimal_does_not():
    def command(value):
        return OperationCommand("test", "manual_journal", 1, date(2026, 10, 8),
            datetime(2026, 10, 8, tzinfo=UTC), (1, 2), canonical_json({"value": value}))
    assert command(Decimal("1.00")).fingerprint == command(Decimal(1)).fingerprint
    assert command(Decimal(1)).fingerprint != command(Decimal(2)).fingerprint
