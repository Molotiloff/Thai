from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from domain import CashRequestKind, Money
from services.cash_requests.parsing import ParsedRequest
from services.expression_calculator import CalcError, evaluate

from .workflow_models import CashRequestDetails


class CashRequestCalculationError(ValueError):
    pass


class CashRequestCalculator:
    def __init__(self, *, whole_units: bool = True):
        self._whole_units = whole_units

    def _round(self, amount: Decimal, precision: int) -> Decimal:
        result = amount.quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)
        if self._whole_units:
            result = result.quantize(Decimal("1"))
        if result <= 0:
            raise CashRequestCalculationError("Сумма слишком мала для точности счёта")
        return result

    def calculate(
        self,
        parsed: ParsedRequest,
        accounts: Sequence[Mapping[str, Any]],
        *,
        expected_codes: tuple[str, ...] | None = None,
    ) -> CashRequestDetails:
        kind = CashRequestKind(parsed.kind)
        if kind in {CashRequestKind.DEPOSIT, CashRequestKind.WITHDRAWAL}:
            code = parsed.code.upper()
            self._validate_codes((code,), expected_codes)
            account = self._account(accounts, code)
            if account is None:
                raise CashRequestCalculationError(
                    f"Счёт {code} не найден. Добавьте валюту: /добавь {code} [точность]"
                )
            amount = self._amount(parsed.amount_expr, plural=False)
            return CashRequestDetails(
                kind=kind,
                money=Money(
                    self._round(amount, self._precision(account)),
                    code,
                    self._precision(account),
                ),
            )

        in_code = parsed.in_code.upper()
        out_code = parsed.out_code.upper()
        if in_code == out_code:
            raise CashRequestCalculationError('Выберите два разных счёта для обмена')
        self._validate_codes((in_code, out_code), expected_codes)
        in_account = self._account(accounts, in_code)
        out_account = self._account(accounts, out_code)
        if in_account is None or out_account is None:
            missing = in_code if in_account is None else out_code
            raise CashRequestCalculationError(
                f"Счёт {missing} не найден. Добавьте: /добавь {missing} [точность]"
            )
        receive = self._amount(parsed.amt_in_expr, plural=True)
        pay = self._amount(parsed.amt_out_expr, plural=True)
        in_precision = self._precision(in_account)
        out_precision = self._precision(out_account)
        return CashRequestDetails(
            kind=kind,
            receive=Money(
                self._round(receive, in_precision),
                in_code,
                in_precision,
            ),
            pay=Money(
                self._round(pay, out_precision),
                out_code,
                out_precision,
            ),
        )

    @staticmethod
    def _amount(expression: str, *, plural: bool) -> Decimal:
        try:
            amount = evaluate(expression)
        except (CalcError, InvalidOperation) as exc:
            raise CashRequestCalculationError(f"Ошибка в выражении суммы: {exc}") from exc
        if amount <= 0:
            message = "Суммы должны быть > 0" if plural else "Сумма должна быть > 0"
            raise CashRequestCalculationError(message)
        return amount

    @staticmethod
    def _account(accounts: Sequence[Mapping[str, Any]], code: str) -> Mapping[str, Any] | None:
        return next(
            (row for row in accounts if str(row["currency_code"]).upper() == code),
            None,
        )

    @staticmethod
    def _precision(account: Mapping[str, Any]) -> int:
        return int(account.get("precision") or 2)

    @staticmethod
    def _validate_codes(actual: tuple[str, ...], expected: tuple[str, ...] | None) -> None:
        if expected is not None and actual != tuple(code.upper() for code in expected):
            raise CashRequestCalculationError("Нельзя менять валюты при редактировании заявки.")
