"""Thai expense read responses. Decimal values travel as strings."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class ExpenseCategory(BaseModel):
    id: int
    name: str
    is_active: bool


class ExpenseParty(BaseModel):
    party_id: int
    code: str
    name: str


class ExpenseParticipant(ExpenseParty):
    client_id: int | None
    group_code: str | None
    client_active: bool | None


class ExpenseShare(ExpenseParty):
    expense_share_usdt: Decimal
    personally_paid_usdt: Decimal


class ExpenseBalance(BaseModel):
    currency_code: str
    payable: Decimal
    receivable: Decimal
    net_payable: Decimal


class ExpenseWalletBalance(ExpenseBalance, ExpenseParty):
    client_id: int
    wallet_balance: Decimal


class ExpenseParticipantBalance(ExpenseBalance):
    party_id: int
    name: str


class ExpenseObligationTotal(BaseModel):
    debtor_party_id: int
    creditor_party_id: int
    currency_code: str
    amount: Decimal
    historical_usdt: Decimal


class ExpenseObligation(BaseModel):
    id: int
    expense_id: int
    currency_code: str
    debtor_party_id: int
    creditor_party_id: int
    amount: Decimal
    original_usdt: Decimal
    business_ledger_account_id: int | None


class ExpenseOpenObligation(ExpenseObligation):
    debtor_name: str
    creditor_name: str
    business_date: date
    source_operation_id: int
    remaining: Decimal
    remaining_usdt: Decimal


class ExpenseDebts(BaseModel):
    expense_shares: list[ExpenseShare]
    wallet_balances: list[ExpenseWalletBalance]
    obligations: list[ExpenseOpenObligation]
    totals: list[ExpenseObligationTotal]
    by_participant: list[ExpenseParticipantBalance]


class ExpenseRecord(BaseModel):
    id: int
    operation_id: int
    category_id: int
    currency_code: str
    amount: Decimal
    amount_usdt: Decimal
    business_usdt: Decimal
    rate_snapshot_id: int
    payer_kind: Literal['cash', 'party']
    payer_party_id: int | None
    cash_instrument_code: str | None
    comment: str | None
    settlement_version: int
    business_date: date


class ExpenseListItem(ExpenseRecord):
    category_name: str
    active: bool


class ExpensePage(BaseModel):
    items: list[ExpenseListItem]
    next_before_id: int | None


class ExpenseInput(BaseModel):
    expense_id: int
    target_code: str
    percent: Decimal


class ExpenseAllocation(BaseModel):
    expense_id: int
    party_id: int
    percent: Decimal
    amount: Decimal
    amount_usdt: Decimal


class ExpenseWalletEntry(BaseModel):
    expense_id: int
    operation_id: int
    participant_code: str
    client_id: int
    wallet_account_id: int
    credit_usdt: Decimal
    debit_usdt: Decimal


class ExpenseCorrection(BaseModel):
    original_expense_id: int
    replacement_expense_id: int
    operation_id: int


class ExpenseDetail(ExpenseRecord):
    created_by: int
    effective_at: datetime
    quoted_rate: Decimal
    usdt_per_unit: Decimal
    cancelled: bool
    business_amount: Decimal
    inputs: list[ExpenseInput]
    allocations: list[ExpenseAllocation]
    obligations: list[ExpenseObligation]
    wallet_entries: list[ExpenseWalletEntry]
    corrections: list[ExpenseCorrection]


class ExpenseStatementRow(BaseModel):
    closure_id: int | None = None
    debtor_party_id: int
    creditor_party_id: int
    currency_code: str
    wallet_mode: bool
    opening: Decimal
    accrued: Decimal
    withheld: Decimal
    other_movements: Decimal
    settled: Decimal
    netted: Decimal
    corrected: Decimal
    closing: Decimal


class ExpenseStatement(BaseModel):
    period: date
    closed: bool
    rows: list[ExpenseStatementRow]
    needs_revision: bool | None = None
    id: int | None = None
    revision: int | None = None
    operation_id: int | None = None
    supersedes_id: int | None = None


class ExpenseRevision(BaseModel):
    id: int
    period: date
    operation_id: int
    supersedes_id: int
    reason: str
    completed_by_operation_id: int | None
