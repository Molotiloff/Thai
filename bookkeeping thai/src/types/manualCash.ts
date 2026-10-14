export type ManualCashAccountCode = 'moscow_poets' | 'moscow_bs';
export type ManualCashOperation = 'opening' | 'inflow' | 'outflow' | 'reversal';

export interface ManualCashAccount {
  code: ManualCashAccountCode;
  name: string;
  balance: string;
}

export interface ManualCashMove {
  id: number;
  accountCode: ManualCashAccountCode;
  operation: ManualCashOperation;
  amount: string;
  balanceAfter: string;
  effectiveAt: string;
  comment: string;
  actorName: string;
  reversalOfId: number | null;
  reversed: boolean;
}

export interface ManualCashSnapshot {
  accounts: ManualCashAccount[];
  moves: ManualCashMove[];
}

export interface RecordManualCashPayload {
  accountCode: ManualCashAccountCode;
  operation: Extract<ManualCashOperation, 'inflow' | 'outflow'>;
  amount: string;
  effectiveAt: string;
  comment: string;
  idempotencyKey: string;
}

export interface ReverseManualCashPayload {
  comment: string;
  idempotencyKey: string;
}
