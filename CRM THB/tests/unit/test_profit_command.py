from decimal import Decimal

import pytest

from domain.finance.models import InvalidPosting
from services.accounting.profit_command import parse_profit_command, telegram_words


@pytest.mark.parametrize('text,amount,client,thb,comment', [
    ('/прибыль 125.50','125.50',None,None,''),
    ('/прибыль -20 ! Убыток','-20',None,None,'Убыток'),
    ('/прибыль 12,12345678 Роджер 100000 ! Старая сделка','12.12345678','Роджер','100000','Старая сделка'),
    ('/прибыль 125.50 100000 ! Клиент неизвестен','125.50',None,'100000','Клиент неизвестен'),
    ('/прибыль 125.50 "Иван Петров"','125.50','Иван Петров',None,''),
    ('/прибыль 125.50 "Клиент 2" 1000,25','125.50','Клиент 2','1000.25',''),
    ('/прибыль 125.50 #42 1000','125.50','#42','1000',''),
    ('/ПРИБЫЛЬ@thai_bot 11.2026','11.2026',None,None,''),
    ('/прибыль 1 "Клиент!" ! Комментарий с \'кавычкой','1','Клиент!',None,"Комментарий с 'кавычкой"),
    ('/прибыль .5 ! <b>текст</b> ! "','0.5',None,None,'<b>текст</b> ! "'),
])
def test_profit_command_optional_arguments(text, amount, client, thb, comment):
    parsed = parse_profit_command(text)
    assert parsed.amount_usdt == Decimal(amount)
    assert parsed.client_reference == client
    assert parsed.thb_amount == (None if thb is None else Decimal(thb))
    assert parsed.comment == comment


@pytest.mark.parametrize('text', ['/прибыль','/прибыль ! комментарий','/прибыль отчет 10.2026',
    '/прибыль 0','/прибыль NaN','/прибыль Infinity','/прибыль 1.000000001',
    '/прибыль 1 -100','/прибыль 1 0','/прибыль 1 100.001','/прибыль 1 "Иван',
    '/прибыль 1 ""','/прибыль 1 ! '+('x'*2001)])
def test_invalid_profit_command_is_rejected(text):
    with pytest.raises(InvalidPosting):
        parse_profit_command(text)


def test_other_command_tokenization_is_unchanged():
    assert telegram_words('/перевод 100 "Иван Петров"') == ['/перевод','100','Иван Петров']
    assert telegram_words('/прибыль 10 ! незакрытая "') == ['/прибыль','10']
    with pytest.raises(ValueError):
        telegram_words('/перевод 100 "Иван')
