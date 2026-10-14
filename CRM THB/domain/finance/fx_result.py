from decimal import Decimal, localcontext

from domain.finance.models import JournalLine, PostingPlan, Side, Valuation


def balance_with_fx(lines: tuple[JournalLine, ...], *, income_account_id: int,
                    loss_account_id: int) -> PostingPlan:
    """Recognize the difference between historical liability and cash valuation."""
    with localcontext() as context:
        context.prec = 80
        difference = sum((line.side.sign * line.usdt for line in lines), Decimal(0))
    if difference > 0:
        lines += (JournalLine(income_account_id, 'USDT', Side.CREDIT, difference, difference, Valuation.BASE),)
    elif difference < 0:
        amount = difference.copy_abs()
        lines += (JournalLine(loss_account_id, 'USDT', Side.DEBIT, amount, amount, Valuation.BASE),)
    return PostingPlan(lines)
