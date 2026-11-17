"""Unambiguous optional client/THB fields and a literal trailing comment."""

import re
import shlex
from dataclasses import dataclass
from decimal import Decimal

from domain.finance.models import InvalidPosting
from services.accounting.manual_profit import ManualProfitService

USAGE = '/прибыль суммаUSDT [имя клиента или #ID] [количество батов] [! комментарий]'
NUMBER = re.compile(r'[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)')


def split_profit_comment(text):
    quote = None
    escaped = False
    for index, character in enumerate(text):
        if escaped:
            escaped = False
        elif character == '\\' and quote != "'":
            escaped = True
        elif quote:
            if character == quote:
                quote = None
        elif character in ('"', "'"):
            quote = character
        elif character == '!':
            return text[:index], text[index+1:].strip()
    return text, ''


def telegram_words(text):
    """Do not parse quotes in the free comment as command argument syntax."""
    first = text.split(maxsplit=1)[0] if text.strip() else ''
    if first.split('@')[0].lower() != '/прибыль':
        return shlex.split(text)
    prefix, _ = split_profit_comment(text)
    try:
        return shlex.split(prefix)
    except ValueError:
        raise InvalidPosting('Закройте кавычки в имени клиента. '+USAGE) from None


@dataclass(frozen=True)
class ProfitCommand:
    amount_usdt: Decimal
    client_reference: str | None
    thb_amount: Decimal | None
    comment: str


def parse_profit_command(text):
    words = telegram_words(text)
    if len(words) < 2 or words[0].split('@')[0].lower() != '/прибыль':
        raise InvalidPosting(USAGE)
    if not NUMBER.fullmatch(words[1]):
        raise InvalidPosting('Укажите сумму USDT числом; минус означает убыток. '+USAGE)
    optional = words[2:]
    thb = None
    if optional and NUMBER.fullmatch(optional[-1]):
        thb = optional.pop().replace(',', '.')
    reference = ' '.join(optional).strip() if optional else None
    if reference == '':
        raise InvalidPosting('Укажите имя клиента или не заполняйте его. '+USAGE)
    _, comment = split_profit_comment(text)
    if len(comment) > 2000:
        raise InvalidPosting('Комментарий: не более 2000 символов')
    attributes = ManualProfitService.attributes(words[1].replace(',', '.'), thb, comment)
    return ProfitCommand(attributes['amount_usdt'], reference, attributes['thb_amount'], attributes['comment'])
