'use server';

import { ApiError } from '@/api/httpClient';
import { dealsService } from '@/services';
import type { CashDealRequest, ClientTransferRequest, ExchangeDealRequest } from '@/types/newDeal';

export async function createCash(payload: CashDealRequest): Promise<
  | { ok: true; dealId: string; requestChatPosted: boolean }
  | { ok: false; message: string }
> {
  try {
    const created = await dealsService.createCash(payload);
    return { ok: true, dealId: created.deal.id, requestChatPosted: created.requestChatPosted === true };
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : 'Не удалось создать кассовую заявку' };
  }
}

export async function createExchange(payload: ExchangeDealRequest): Promise<
  | { ok: true; dealId: string; requestChatPosted: boolean }
  | { ok: false; message: string }
> {
  try {
    const created = await dealsService.createExchange(payload);
    return { ok: true, dealId: created.deal.id, requestChatPosted: created.requestChatPosted === true };
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : 'Не удалось создать обмен' };
  }
}

export async function createClientTransfer(payload: ClientTransferRequest): Promise<
  | { ok: true; dealId: string }
  | { ok: false; insufficient: boolean; message: string }
> {
  try {
    const created = await dealsService.createClientTransfer(payload);
    return { ok: true, dealId: created.deal.id };
  } catch (error) {
    return {
      ok: false,
      insufficient: error instanceof ApiError && error.status === 409
        && error.message.includes('Недостаточно средств'),
      message: error instanceof Error ? error.message : 'Не удалось провести перевод',
    };
  }
}
