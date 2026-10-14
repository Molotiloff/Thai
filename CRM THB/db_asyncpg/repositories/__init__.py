"""Public exports are loaded on demand; importing a port must not boot RF adapters."""

from importlib import import_module

_EXPORTS = {
    'ActCounterRepo': ('act_counter', 'ActCounterRepo'),
    'ClientsRepo': ('clients', 'ClientsRepo'),
    'ExchangeRequestsRepo': ('exchange_requests', 'ExchangeRequestsRepo'),
    'FirmPositionsRepo': ('firm_positions', 'FirmPositionsRepo'),
    'LiveMessagesRepo': ('live_messages', 'LiveMessagesRepo'),
    'ManagersRepo': ('managers', 'ManagersRepo'),
    'PaymentWatchRepo': ('payment_watch', 'PaymentWatchRepo'),
    'RequestScheduleRepo': ('request_schedule', 'RequestScheduleRepo'),
    'SettingsRepo': ('settings', 'SettingsRepo'),
    'TransactionsRepo': ('transactions', 'TransactionsRepo'),
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
    "ActCounterRepo",
    "ClientsRepo",
    "ExchangeRequestsRepo",
    "FirmPositionsRepo",
    "LiveMessagesRepo",
    "ManagersRepo",
    "PaymentWatchRepo",
    "RequestScheduleRepo",
    "SettingsRepo",
    "TransactionsRepo",
]
