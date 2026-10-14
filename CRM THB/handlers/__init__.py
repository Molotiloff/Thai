"""Load only the requested handler; package imports must not boot integrations."""

from importlib import import_module

_HANDLERS = {
    'XEHandler': 'xe',
    'AcceptShortHandler': 'accept_short',
    'ActHandler': 'act',
    'AdminRequestHandler': 'admin_request',
    'AMLHandler': 'aml',
    'ClientsBalancesHandler': 'balances_clients',
    'BestChangeHandler': 'best_change',
    'BroadcastAllHandler': 'broadcast_all',
    'CalcHandler': 'calc',
    'CashRequestsHandler': 'cash_requests',
    'ClientsHandler': 'clients',
    'debug_router': 'debug',
    'ManagersHandler': 'managers',
    'NonZeroHandler': 'nonzero',
    'PaymentWatchHandler': 'payment_watch',
    'StartHandler': 'start',
    'UsdtWalletHandler': 'usdt_wallet',
    'WalletsHandler': 'wallets',
}


def __getattr__(name):
    module = _HANDLERS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


__all__ = [
    "AMLHandler",
    "AcceptShortHandler",
    "ActHandler",
    "AdminRequestHandler",
    "BestChangeHandler",
    "BroadcastAllHandler",
    "CalcHandler",
    "CashRequestsHandler",
    "ClientsBalancesHandler",
    "ClientsHandler",
    "ManagersHandler",
    "NonZeroHandler",
    "PaymentWatchHandler",
    "StartHandler",
    "UsdtWalletHandler",
    "WalletsHandler",
    "XEHandler",
    "debug_router",
]
