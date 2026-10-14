from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.exchange.keyboard_port import ExchangeKeyboardPort


class AiogramExchangeKeyboardPresenter(ExchangeKeyboardPort):
    def client_cancel(
        self,
        *,
        request_id: int | str,
        table_request_id: int | str | None,
    ) -> InlineKeyboardMarkup:
        callback_data = f"req_cancel:{request_id}"
        if table_request_id is not None:
            callback_data = f"{callback_data}:{table_request_id}"
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Отменить заявку",
                        callback_data=callback_data,
                    )
                ]
            ]
        )

    def request_chat(
        self,
        *,
        request_id: int | str,
        table_request_id: int | str,
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Отменить заявку",
                        callback_data=f"req_cancel:{request_id}:{table_request_id}",
                    ),
                ]
            ]
        )


