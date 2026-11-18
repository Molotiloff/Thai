import pytest

from domain.deal_policy import TRADE_LABELS, baht_trade_type, exchange_type


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
def test_historical_contract_codes_remain_stable(receive, pay, expected):
    assert exchange_type(receive, pay) == expected


@pytest.mark.parametrize('receive,pay,kind,label', [
    ('USDT','THB_CASH','sale','Продажа'),
    ('EUR','THB_BANK','sale','Продажа'),
    ('THB_CASH','USDT','purchase','Покупка'),
    ('THB_BANK','RUB','purchase','Покупка'),
    ('THB_CASH','THB_BANK','conversion','Конвертация'),
    ('USD_WH','USDT','conversion','Конвертация'),
])
def test_business_labels_are_about_buying_and_selling_baht(receive,pay,kind,label):
    assert baht_trade_type(receive,pay) == kind
    assert TRADE_LABELS[kind] == label
