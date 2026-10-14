from __future__ import annotations

import pytest

from api.schemas.deals import CashDealCreateRequest
from services.cash_requests.constants import ACCOUNTING_FX_CMD_MAP, CMD_MAP
from services.cash_requests.parsing import parse_dep_wd, parse_fx


@pytest.mark.parametrize(
    ("command", "kind"),
    [("депбат", "dep"), ("выдбат", "wd")],
)
def test_baht_cash_commands(command: str, kind: str) -> None:
    parsed = parse_dep_wd(
        f"/{command} екб 1000",
        cmd_map=CMD_MAP,
        city_keys={"екб"},
        default_city="екб",
    )

    assert parsed is not None
    assert parsed.kind == kind
    assert parsed.code == "THB"
    assert parsed.amount_expr == "1000"


def test_crm_cash_request_normalizes_time_and_optional_contacts() -> None:
    payload = CashDealCreateRequest.model_validate(
        {
            "dealType": "withdrawal",
            "clientId": 1,
            "city": "екб",
            "currency": "THB",
            "amount": "1000",
            "idempotencyKey": "test-cash-baht",
            "time": "9:05",
            "contact1": None,
            "contact2": None,
        }
    )

    assert payload.time == "09:05"
    assert payload.contact1 is None


@pytest.mark.parametrize('command,definition',ACCOUNTING_FX_CMD_MAP.items())
def test_cash_exchange_commands_preserve_all_eight_instruments(command,definition):
    parsed = parse_fx(f'/{command}@testbot 100*2 3000 @cashier @client ! обмен',
        fx_cmd_map=ACCOUNTING_FX_CMD_MAP,city_keys=set(),default_city='')
    assert parsed is not None
    assert (parsed.kind,parsed.in_code,parsed.out_code)==definition
    assert parsed.city=='' and parsed.amt_in_expr=='100*2' and parsed.amt_out_expr=='3000'
    assert parsed.contact1=='@cashier' and parsed.contact2=='@client' and parsed.comment=='обмен'


def test_cash_exchange_ruble_alias_does_not_silently_mean_baht():
    assert len(ACCOUNTING_FX_CMD_MAP)==56
    assert ACCOUNTING_FX_CMD_MAP['првд']==('fx','RUB','USD_BL')
    assert ACCOUNTING_FX_CMD_MAP['пбатвд']==('fx','THB_CASH','USD_BL')
    assert ACCOUNTING_FX_CMD_MAP['пбезналвд']==('fx','THB_BANK','USD_BL')
