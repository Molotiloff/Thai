"""Contractual THB spread paid to a counterparty's USDT wallet."""
from decimal import localcontext

from domain.deal_policy import exchange_type
from domain.finance.models import InvalidPosting, decimal, exact, rounded


def counterparty_attributes(receive, pay, client_id=None, spread=None):
    amount = exact(decimal(spread or '0'))
    if client_id is None and amount == 0:
        return {}
    if client_id is None or isinstance(client_id, bool) or not isinstance(client_id, int) or client_id <= 0:
        raise InvalidPosting('Выберите КТ — существующий клиентский чат')
    if amount <= 0:
        raise InvalidPosting('Прибыль КТ должна быть положительной')
    if exchange_type(receive['instrument'], pay['instrument']) not in ('sale', 'purchase'):
        raise InvalidPosting('КТ доступен только в покупке и продаже батов')
    return {'counterparty_client_id': client_id, 'counterparty_spread': amount}


def counterparty_result(attributes, quotes, precision=6):
    if 'counterparty_client_id' not in attributes:
        return None
    receives_thb = exchange_type(attributes['receive']['instrument'], attributes['pay']['instrument']) == 'sale'
    thb_side, other_side = ('receive', 'pay') if receives_thb else ('pay', 'receive')
    thb = decimal(attributes[thb_side]['quantity'])
    usdt = quotes[other_side].value(decimal(attributes[other_side]['quantity']))
    if usdt <= 0:
        raise InvalidPosting('Сумма обмена слишком мала для расчёта КТ')
    spread = decimal(attributes['counterparty_spread'])
    with localcontext() as context:
        context.prec = 80
        rate = thb / usdt
        # The UI operation names classify the opposite currency. Follow the
        # actual THB movement: pay THB => sell baht, receive THB => buy baht.
        company_rate = rate + spread if receives_thb else rate - spread
        if company_rate <= 0:
            raise InvalidPosting('Спред КТ должен быть меньше курса продажи')
        amount = rounded(usdt * spread / rate, precision)
        if amount <= 0:
            raise InvalidPosting('Начисление КТ меньше минимальной суммы USDT')
        return {'amount_usdt': amount, 'spread_thb': rounded(usdt * spread),
                'client_rate': rounded(rate, 18), 'company_rate': rounded(company_rate, 18)}
