import pytest

from domain.deal_policy import exchange_type


@pytest.mark.parametrize("receive,pay,expected", [
    ("THB_CASH", "USDT", "sale"),
    ("THB_BANK", "USD_WH", "sale"),
    ("THB_CASH", "RUB", "sale"),
    ("USDT", "THB_BANK", "purchase"),
    ("USD_BL", "THB_CASH", "purchase"),
    ("RUB", "THB_BANK", "purchase"),
    ("USD_WH", "USDT", "conversion"),
    ("USDT", "RUB", "conversion"),
    ("RUB", "EUR500", "conversion"),
    ("EUR", "USD_BL", "conversion"),
    ("THB_CASH", "THB_BANK", "conversion"),
    ("THB_BANK", "THB_CASH", "conversion"),
])
def test_exchange_type_from_firm_perspective(receive, pay, expected):
    assert exchange_type(receive, pay) == expected
