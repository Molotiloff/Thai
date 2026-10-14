"""Public exports are loaded on demand; importing a port must not boot RF adapters."""

from importlib import import_module

_EXPORTS = {
    'CashChatRegistrySyncService': ('cash_chat_registry_service', 'CashChatRegistrySyncService'),
    'FirmPositionAccountingService': ('firm_position_service', 'FirmPositionAccountingService'),
    'ManualCashService': ('manual_cash_service', 'ManualCashService'),
    'AccrueUsdtProfit': ('models', 'AccrueUsdtProfit'),
    'AllocatePartnerPurchase': ('models', 'AllocatePartnerPurchase'),
    'ManualCashAccount': ('models', 'ManualCashAccount'),
    'ManualCashAccountCode': ('models', 'ManualCashAccountCode'),
    'ManualCashMove': ('models', 'ManualCashMove'),
    'ManualCashOperation': ('models', 'ManualCashOperation'),
    'ManualProfitQuote': ('models', 'ManualProfitQuote'),
    'RecordAdjustment': ('models', 'RecordAdjustment'),
    'RecordManualCashMove': ('models', 'RecordManualCashMove'),
    'RecordOpening': ('models', 'RecordOpening'),
    'RecordProfitCapitalization': ('models', 'RecordProfitCapitalization'),
    'RecordPurchase': ('models', 'RecordPurchase'),
    'RecordSale': ('models', 'RecordSale'),
    'ReverseManualCashMove': ('models', 'ReverseManualCashMove'),
    'ReversePositionMove': ('models', 'ReversePositionMove'),
    'SettlePartnerTransfer': ('models', 'SettlePartnerTransfer'),
    'PartnerPurchaseAllocationService': ('partner_allocation_service', 'PartnerPurchaseAllocationService'),
    'MidnightProfitCapitalizationJob': ('profit_service', 'MidnightProfitCapitalizationJob'),
    'ProfitAccrualService': ('profit_service', 'ProfitAccrualService'),
    'ProfitValuationService': ('profit_service', 'ProfitValuationService'),
    'WalletFactService': ('wallet_fact_service', 'WalletFactService'),
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
    "AccrueUsdtProfit",
    "AllocatePartnerPurchase",
    "CashChatRegistrySyncService",
    "FirmPositionAccountingService",
    "ManualCashAccount",
    "ManualCashAccountCode",
    "ManualCashMove",
    "ManualCashOperation",
    "ManualCashService",
    "ManualProfitQuote",
    "MidnightProfitCapitalizationJob",
    "PartnerPurchaseAllocationService",
    "ProfitAccrualService",
    "ProfitValuationService",
    "RecordAdjustment",
    "RecordManualCashMove",
    "RecordOpening",
    "RecordProfitCapitalization",
    "RecordPurchase",
    "RecordSale",
    "ReverseManualCashMove",
    "ReversePositionMove",
    "SettlePartnerTransfer",
    "WalletFactService",
]
