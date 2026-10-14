from __future__ import annotations

import html
from decimal import Decimal


def _fmt(amount: Decimal, precision: int = 3) -> str:
    q = amount.quantize(Decimal(1).scaleb(-precision))
    return f"{q:,.{precision}f}".replace(",", "’")


class PaymentWatchMessageBuilder:
    def __init__(self, minimum_confirmations: int = 1, precision: int = 3):
        self.minimum_confirmations = minimum_confirmations
        self.precision = precision

    def build_started(self, *, address: str, test_mode: bool, manager_note: str | None = None) -> str:
        safe = html.escape(address)
        note_line = f"\nКомментарий менеджера: <code>{html.escape(manager_note)}</code>" if manager_note else ""
        if test_mode:
            return (
                "❗❗❗ Если указанный адрес принадлежит бирже\n"
                "(например, Bybit), пожалуйста, сообщите об этом менеджеру\n"
                "перед отправкой.❗❗❗\n"
                "⏳ Начал отслеживание отправки.\n"
                f"Адрес: <code>{safe}</code>\n"
                "Сценарий: тестовая 1 USDT, затем основная.\n"
                "Ищу переводы только между кошельками.\n"
                f"Подтверждение: {self.minimum_confirmations} confirmations."
                f"{note_line}"
            )
        return (
            "❗❗❗ Если указанный адрес принадлежит бирже\n"
            "(например, Bybit), пожалуйста, сообщите об этом менеджеру\n"
            "перед отправкой.❗❗❗\n"
            "⏳ Начал отслеживание отправки.\n"
            f"Адрес: <code>{safe}</code>\n"
            "Жду перевод USDT TRC-20  между кошельками.\n"
            f"Подтверждение: {self.minimum_confirmations} confirmations."
            f"{note_line}"
        )

    def build_test_success(
        self,
        *,
        amount: Decimal,
        tx_hash: str,
        from_address: str,
        to_address: str,
        block_number: int | None,
    ) -> str:
        block_line = (
            f"Номер блока: <code>{block_number}</code>\n"
            if block_number is not None
            else ""
        )
        return (
            "🚀 Тестовая транзакция прошла успешно.\n"
            f"Кошелёк отправителя: <code>{html.escape(from_address)}</code>\n"
            f"Кошелёк получателя: <code>{html.escape(to_address)}</code>\n"
            f"💸: <code>{_fmt(amount,self.precision)} USDT</code>\n"
            f"{block_line}"
            f"🫆 Хэш: <code>{html.escape(tx_hash)}</code>\n"
            "Ожидаю основную транзакцию."
        )

    def build_main_success(
        self,
        *,
        amount: Decimal,
        tx_hash: str,
        direction: str = "IN",
    ) -> str:
        tx_url = f"https://tronscan.org/#/transaction/{html.escape(tx_hash, quote=True)}"
        action = "получены" if direction == "OUT" else "переведены"
        return (
            f"🚀 Средства {action}:\n"
            f"💸 <code>{_fmt(amount,self.precision)} USDT</code>\n"
            f"🔗 <a href=\"{tx_url}\">Ссылка на Tronscan</a>"
        )

    def build_settlement_review(
        self,
        *,
        expected: Decimal,
        actual: Decimal,
        tx_hash: str,
    ) -> str:
        tx_url = f"https://tronscan.org/#/transaction/{html.escape(tx_hash, quote=True)}"
        return (
            "⚠️ Платёж подтверждён, но сумма требует проверки.\n"
            f"Ожидалось: <code>{_fmt(expected,self.precision)} USDT</code>\n"
            f"Фактически: <code>{_fmt(actual,self.precision)} USDT</code>\n"
            f"🔗 <a href=\"{tx_url}\">Ссылка на Tronscan</a>"
        )

    def build_timeout(self) -> str:
        return "⌛ Отправка не подтверждена за 15 минут. Продолжить ожидание?"

    def build_continued(self) -> str:
        return "⏳ Ожидание отправки продлено ещё на 15 минут."

    def build_stopped(self) -> str:
        return "⛔ Ожидание отправки остановлено."


    def build_accounting(self, watch):
        if watch['status']=='TIMED_OUT':
            return self.build_timeout()
        if watch['status']=='STOPPED':
            return self.build_stopped()
        events = watch['events']
        if watch['status']=='COMPLETED':
            main = next(event for event in reversed(events) if event['event_type']=='MAIN')
            if watch['review_required']:
                return self.build_settlement_review(expected=watch['expected_quantity'],actual=main['amount'],tx_hash=main['tx_hash'])+'\nФактическая сумма учтена в кассе и кошельке.'
            return self.build_main_success(amount=main['amount'],tx_hash=main['tx_hash'],direction='IN')
        test = next((event for event in events if event['event_type']=='TEST'),None)
        if test:
            return self.build_test_success(amount=test['amount'],tx_hash=test['tx_hash'],
                from_address=watch['our_address'],to_address=watch['address'],block_number=None)
        return self.build_started(address=watch['address'],test_mode=watch['mode']=='TEST_THEN_MAIN',
            manager_note=str(watch['expected_quantity']))
