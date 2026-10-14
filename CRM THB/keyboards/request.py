from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.cash_requests.constants import (
    CB_DEAL_CANCEL,
    CB_DEAL_DONE,
    CB_DEAL_READY,
    CB_ISSUE_DONE,
)

CB_PARTNER = "req:partner"


def deal_kb(req_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Готово к расчету",
                    callback_data=f"{CB_DEAL_READY}{req_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Сделка отменена",
                    callback_data=f"{CB_DEAL_CANCEL}{req_id}",
                )
            ],
        ]
    )


def cash_completion_kb(req_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Сделка завершена",
                    callback_data=f"{CB_DEAL_DONE}{req_id}",
                )
            ]
        ]
    )






def issue_keyboard(
    req_id: int | str,
    kind: str,
    cb_issue_done: str = CB_ISSUE_DONE,
) -> InlineKeyboardMarkup:
    """
    Кнопка «Выдано» для клиентского чата.
    Формат: req:issue_done:<KIND>:<REQ_ID>
    """
    cb_value = f"{cb_issue_done}:{kind}:{req_id}"
    rows = [[InlineKeyboardButton(text="Выдано", callback_data=cb_value)]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


