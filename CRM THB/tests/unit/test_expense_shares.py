from decimal import Decimal

import pytest

from domain.expenses import allocate_shares, settle_fifo
from domain.finance.models import InvalidPosting

PARTIES = {"skyex": 2, "pe": 3, "daniyar": 4}


def test_common_and_rounding_native_and_usdt():
    rows, _ = allocate_shares(Decimal(".01"), Decimal(".00028571"), 2, {"common": "100"}, PARTIES)
    assert sum(row.amount for row in rows) == Decimal(".01")
    assert sum(row.usdt for row in rows) == Decimal(".00028571")
    assert next(row for row in rows if row.code == "skyex").amount == Decimal(".01")
    assert all(row.usdt == 0 for row in rows if row.amount == 0)


@pytest.mark.parametrize(
    "shares",
    [
        {"common": "99"},
        {"pe": "101", "skyex": "-1"},
        {"pe": "100", "other": "0"},
        {"common": "NaN"},
        {"pe": 100.0},
    ],
)
def test_invalid_percentages_rejected(shares):
    with pytest.raises(InvalidPosting):
        allocate_shares(Decimal(1000), Decimal(30), 2, shares, PARTIES)


def test_half_of_millionth_percent_and_large_native_amount():
    amount = Decimal("12345678901234567890123456789.12")
    rows, _ = allocate_shares(
        amount, amount, 2, {"common": "99.999999", "daniyar": ".000001"}, PARTIES
    )
    assert next(row for row in rows if row.code == "pe").percent == Decimal("49.9999995")
    # Ambient Decimal precision may be smaller than a legitimate money amount.
    from decimal import localcontext

    with localcontext() as context:
        context.prec = 80
        assert sum(row.amount for row in rows) == amount
        assert sum(row.usdt for row in rows) == amount


def test_fifo_last_piece_clears_exact_historical_cost():
    row = {
        "id": 1,
        "remaining": Decimal(3),
        "remaining_usdt": Decimal(".00000001"),
        "business_ledger_account_id": 10,
        "source_operation_id": 1,
    }
    first = settle_fifo([row], Decimal(1))[0]
    assert first.usdt == 0
    last = settle_fifo([{**row, "remaining": Decimal(2)}], Decimal(2))[0]
    assert last.usdt == Decimal(".00000001")
