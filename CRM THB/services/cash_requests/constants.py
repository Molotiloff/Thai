from __future__ import annotations

CB_ISSUE_DONE = "req:issue_done"
CB_DEAL_READY = "cash:deal_ready:req:"
CB_DEAL_DONE = "cash:deal_done:req:"
CB_DEAL_CANCEL = "cash:deal_cancel:req:"

CMD_MAP: dict[str, tuple[str, str]] = {
    "депр": ("dep", "RUB"),
    "депт": ("dep", "USDT"),
    "депд": ("dep", "USD"),
    "депе": ("dep", "EUR"),
    "депб": ("dep", "USDW"),
    "депбат": ("dep", "THB"),
    "выдр": ("wd", "RUB"),
    "выдт": ("wd", "USDT"),
    "выдд": ("wd", "USD"),
    "выде": ("wd", "EUR"),
    "выдб": ("wd", "USDW"),
    "выдбат": ("wd", "THB"),
}

FX_CMD_MAP: dict[str, tuple[str, str, str]] = {
    "првд": ("fx", "RUB", "USD"),
    "пдвр": ("fx", "USD", "RUB"),
    "прве": ("fx", "RUB", "EUR"),
    "певр": ("fx", "EUR", "RUB"),
    "првб": ("fx", "RUB", "USDW"),
    "пбвр": ("fx", "USDW", "RUB"),
    "првп": ("fx", "RUB", "EUR500"),
    "ппвр": ("fx", "EUR500", "RUB"),
    "пдве": ("fx", "USD", "EUR"),
    "певд": ("fx", "EUR", "USD"),
    "пдвб": ("fx", "USD", "USDW"),
    "пбвд": ("fx", "USDW", "USD"),
    "пдвп": ("fx", "USD", "EUR500"),
    "ппвд": ("fx", "EUR500", "USD"),
    "певб": ("fx", "EUR", "USDW"),
    "пбве": ("fx", "USDW", "EUR"),
    "певп": ("fx", "EUR", "EUR500"),
    "ппве": ("fx", "EUR500", "EUR"),
    "пбвп": ("fx", "USDW", "EUR500"),
    "ппвб": ("fx", "EUR500", "USDW"),
}


ACCOUNTING_CASH_SUFFIXES = {
    "бат":"THB_CASH", "безнал":"THB_BANK", "д":"USD_BL", "б":"USD_WH",
    "т":"USDT", "е":"EUR", "п":"EUR500", "р":"RUB",
}
ACCOUNTING_CMD_MAP = {
    prefix+suffix: (kind, code) for prefix,kind in (("деп","dep"),("выд","wd"))
    for suffix,code in ACCOUNTING_CASH_SUFFIXES.items()
}

# Preserve RUB as a foreign currency; THB cash/bank have explicit suffixes.
ACCOUNTING_FX_CMD_MAP = {
    f'п{incoming}в{outgoing}': ('fx', receive, pay)
    for incoming, receive in ACCOUNTING_CASH_SUFFIXES.items()
    for outgoing, pay in ACCOUNTING_CASH_SUFFIXES.items()
    if receive != pay
}
