from __future__ import annotations

import html
from decimal import Decimal

from services.number_formatting import format_amount_core, format_amount_with_sign
from services.wallets.compact_formatter import format_wallet_compact, label_for


class WalletTextBuilder:
    @staticmethod
    def wallet_text(*, chat_name: str, rows: list[dict]) -> str:
        safe_title = html.escape(f"Средств у {chat_name}:")
        safe_rows = html.escape(format_wallet_compact(rows, only_nonzero=False))
        return f"<code>{safe_title}\n\n{safe_rows}</code>"

    @staticmethod
    def remove_currency_confirmation(*, code: str, balance: Decimal, precision: int) -> str:
        pretty_bal = format_amount_core(balance, precision)
        warn = ""
        if balance != 0:
            warn = (
                f"\n⚠️ Внимание: баланс по {code} не нулевой ({pretty_bal} {label_for(code)}). "
                f"Удаление допустимо — остаток будет потерян."
            )
        return f"Вы уверены, что хотите удалить валюту {code}?{warn}"

    @staticmethod
    def currency_change_success(
        *,
        code: str,
        delta: Decimal,
        precision: int,
        sign: str,
        balance: Decimal,
    ) -> str:
        pretty_amt = format_amount_with_sign(delta, precision, sign=sign)
        pretty_bal = format_amount_core(balance, precision)
        return f"Запомнил. {pretty_amt}\nБаланс: {pretty_bal} {label_for(code)}"

    @staticmethod
    def undo_already_done_with_balance(*, code: str, balance: Decimal, precision: int) -> str:
        pretty_bal = format_amount_core(balance, precision)
        return f"Операция уже отменена\nБаланс: {pretty_bal} {label_for(code)}"

    @staticmethod
    def undo_success(
        *,
        code: str,
        amount: Decimal,
        precision: int,
        applied_sign: str,
        balance: Decimal,
    ) -> str:
        pretty_delta = format_amount_with_sign(amount, precision, sign=applied_sign)
        pretty_bal = format_amount_core(balance, precision)
        return f"Запомнил. {pretty_delta}\nБаланс: {pretty_bal} {label_for(code)}"


    @staticmethod
    def statement_text(instrument, rows):
        from zoneinfo import ZoneInfo
        precision = 6 if instrument=='USDT' else 2
        lines = [f'Выписка: {label_for(instrument)} · Asia/Bangkok']
        for row in rows:
            timestamp = row['effective_at'].astimezone(ZoneInfo('Asia/Bangkok'))
            amount = format_amount_core(row['quantity_delta'],precision)
            balance = format_amount_core(row['balance_after'],precision)
            comment = row.get('reason') or row.get('command_details',{}).get('comment','')
            comment = comment.replace('\n',' ')[:100]
            lines.append(f"#{row['id']} {timestamp:%d.%m.%Y %H:%M}: {amount}, остаток {balance}"+(f' · {comment}' if comment else ''))
        if not rows:
            lines.append('Движений нет')
        return '\n'.join(lines)
