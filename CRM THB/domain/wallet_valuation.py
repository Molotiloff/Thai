from decimal import Decimal, localcontext

from domain.finance.models import FxQuote, InvalidPosting, rounded


def liability_change(balance: Decimal, carrying: Decimal, change: Decimal,
                     quote: FxQuote | None) -> Decimal:
    """Offset an opposite signed wallet at its carrying cost; value new debt by quote."""
    if change == 0:
        raise InvalidPosting('Изменение кошелька должно быть ненулевым')
    with localcontext() as context:
        context.prec = 80
        sign = Decimal(1) if change > 0 else Decimal(-1)
        offset = min(abs(change), abs(balance)) if balance * change < 0 else Decimal(0)
        historical = Decimal(0)
        if offset:
            historical = abs(carrying) if offset == abs(balance) else rounded(abs(carrying) * offset / abs(balance))
        remainder = abs(change)-offset
        if remainder and quote is None:
            raise InvalidPosting('Для нового или отрицательного остатка требуется ручной курс')
        return sign * (historical + (quote.value(remainder) if remainder else Decimal(0)))
