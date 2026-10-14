"""Public exports are loaded on demand; importing a port must not boot RF adapters."""

from importlib import import_module

_EXPORTS = {
    'AiogramMessenger': ('aiogram_messenger', 'AiogramMessenger'),
    'AiogramCallbackReplier': ('aiogram_repliers', 'AiogramCallbackReplier'),
    'AiogramMessageReplier': ('aiogram_repliers', 'AiogramMessageReplier'),
    'AiogramBroadcastDelivery': ('broadcast_delivery', 'AiogramBroadcastDelivery'),
    'AiogramBroadcastPresenter': ('broadcast_presenter', 'AiogramBroadcastPresenter'),
    'AiogramBroadcastSessionStore': ('broadcast_session_store', 'AiogramBroadcastSessionStore'),
    'AiogramCashKeyboardPresenter': ('cash_keyboards', 'AiogramCashKeyboardPresenter'),
    'ChatLockRegistry': ('chat_locks', 'ChatLockRegistry'),
    'CityCashMediaStore': ('city_cash_media_store', 'CityCashMediaStore'),
    'AiogramExchangeKeyboardPresenter': ('exchange_keyboards', 'AiogramExchangeKeyboardPresenter'),
    'AiogramOrderbookLiveMessageEditor': ('orderbook_live_message_editor', 'AiogramOrderbookLiveMessageEditor'),
    'AiogramPaymentWatchNotifier': ('payment_watch_notifier', 'AiogramPaymentWatchNotifier'),
    'AiogramPaymentWatchPresenter': ('payment_watch_presenter', 'AiogramPaymentWatchPresenter'),
    'AiogramWalletKeyboardPresenter': ('wallet_keyboards', 'AiogramWalletKeyboardPresenter'),
}


def __getattr__(name):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = target
    value = getattr(import_module(f".{module}", __name__), attribute)
    globals()[name] = value
    return value


__all__ = [
    "AiogramBroadcastDelivery",
    "AiogramBroadcastPresenter",
    "AiogramBroadcastSessionStore",
    "AiogramCallbackReplier",
    "AiogramCashKeyboardPresenter",
    "AiogramExchangeKeyboardPresenter",
    "AiogramMessageReplier",
    "AiogramMessenger",
    "AiogramOrderbookLiveMessageEditor",
    "AiogramPaymentWatchNotifier",
    "AiogramPaymentWatchPresenter",
    "AiogramWalletKeyboardPresenter",
    "ChatLockRegistry",
    "CityCashMediaStore",
]
