'use server';

import { randomUUID } from 'node:crypto';
import { revalidatePath } from 'next/cache';
import { mainDashboardService } from '@/services';
import type { ManualCashAccountCode, RecordManualCashPayload } from '@/types/manualCash';

export interface ManualCashActionState {
  error?: string;
  success?: string;
  nextIdempotencyKey?: string;
}

export async function recordManualCashAction(_: ManualCashActionState, data: FormData): Promise<ManualCashActionState> {
  try {
    await mainDashboardService.recordManualCash({
      accountCode: accountCode(data), operation: operation(data),
      amount: required(data, 'amount').replace(',', '.'), effectiveAt: required(data, 'effectiveAt'),
      comment: required(data, 'comment'), idempotencyKey: required(data, 'idempotencyKey'),
    });
    revalidatePath('/');
    return {
      success: 'Операция проведена',
      nextIdempotencyKey: `crm:${randomUUID()}:record`,
    };
  } catch (error) { return { error: error instanceof Error ? error.message : 'Не удалось провести операцию' }; }
}

function accountCode(data: FormData): ManualCashAccountCode {
  const value = required(data, 'accountCode');
  if (value !== 'moscow_poets' && value !== 'moscow_bs') {
    throw new Error('Неизвестный кассовый счёт');
  }
  return value;
}

function operation(data: FormData): RecordManualCashPayload['operation'] {
  const value = required(data, 'operation');
  if (value !== 'inflow' && value !== 'outflow') {
    throw new Error('Неизвестный тип операции');
  }
  return value;
}

export async function reverseManualCashAction(moveId: number, _: ManualCashActionState, data: FormData): Promise<ManualCashActionState> {
  try {
    await mainDashboardService.reverseManualCash(moveId, {
      comment: required(data, 'comment'),
      idempotencyKey: required(data, 'idempotencyKey'),
    });
    revalidatePath('/');
    return { success: 'Операция сторнирована' };
  } catch (error) { return { error: error instanceof Error ? error.message : 'Не удалось сторнировать операцию' }; }
}

function required(data: FormData, name: string): string {
  const value = String(data.get(name) ?? '').trim();
  if (!value) throw new Error('Заполните все обязательные поля');
  return value;
}
