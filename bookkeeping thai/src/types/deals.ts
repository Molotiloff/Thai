import type { CurrencyCode, DealStatus } from './domain';

/**
 * Типы страницы «Сделки»: kanban текущих сделок, сводка по статусам
 * и общий реестр с пагинацией.
 */

export type DealType = 'Покупка' | 'Продажа' | 'BestChange' | 'Перевод' | 'Конвертация';

export type DealAsset = 'RUB' | 'USDT' | 'USD' | 'USDW' | 'EUR' | 'EUR500' | 'THB' | 'BTC' | 'ETH';

export interface DealItem {
  id: string;
  /** Полное имя для таблицы, например «Алексей Смирнов» */
  clientName: string;
  /** Короткое имя для kanban-карточки, например «Алексей С.» */
  clientShortName: string;
  dealType: DealType;
  asset: DealAsset;
  direction?: string;
  amountRub: number;
  transferAmount?: string | null;
  exchangeAmount?: string | null;
  city: string;
  status: DealStatus;
  /** Плашка «на откуп» для balance_check, считается backend поверх act-остатка */
  insufficientUsdt?: boolean;
  /** Человекочитаемое время обновления: «2 мин назад», «Сегодня 10:30» */
  updatedLabel: string;
  createdBy: string;
  /** Показывать ли сделку на kanban-доске текущих сделок */
  onKanban: boolean;
}

/** Направление сделки для таблицы и поиска: «USDT → RUB» */
export function dealDirection(deal: DealItem): string {
  return deal.direction ?? `${deal.asset} → RUB`;
}

/** Подпись операции для kanban-карточки: «Продажа USDT» */
export function dealOperation(deal: DealItem): string {
  if (deal.dealType === 'Конвертация') return `Конвертация ${dealDirection(deal)}`;
  return `${deal.dealType} ${deal.asset}`;
}

export interface DealStatusSummary {
  status: DealStatus;
  count: number;
  /** Сумма по статусу; null — сумма не считается (выводится «—») */
  totalRub: number | null;
}

export interface DealsPageData {
  summaries: DealStatusSummary[];
  cities: string[];
  deals: DealItem[];
}

export interface DealLeg {
  id: string;
  direction: 'IN' | 'OUT';
  currency: CurrencyCode;
  amount: number;
  rate?: number;
  status: 'ACTIVE' | 'CANCELED';
}

export interface DealStatusEvent {
  id: string;
  oldStatus: DealStatus | null;
  newStatus: DealStatus;
  actorName: string;
  createdAt: string;
  comment?: string;
}

export interface DealDetails {
  deal: DealItem;
  dealNo: string;
  createdAt: string;
  updatedAt: string;
  dealAt?: string | null;
  source: 'tg_bot' | 'crm' | 'sheets';
  requestChatPosted?: boolean | null;
  sourceKind?:
    | 'exchange'
    | 'cash'
    | 'fulfillment'
    | 'partner'
    | 'best_change'
    | 'accounting_import'
    | 'client_transfer';
  counterpartyName?: string;
  counterpartyPercent?: number;
  referrerRewardRub?: number | null;
  profitRub: number;
  comment?: string;
  tronscanUrl?: string;
  body?: Record<string, unknown>;
  bestChange?: BestChangeDetails | null;
  legs: DealLeg[];
  statusEvents: DealStatusEvent[];
}

export interface BestChangeDetails {
  operation: 'purchase' | 'sale' | 'unknown';
  qtyUsdt?: number | null;
  marketRateRub?: number | null;
  clientRateRub?: number | null;
  unitSpreadRub?: number | null;
  grossSpreadRub?: number | null;
  profitPoolRub?: number | null;
  partnerShareRub?: number | null;
  skyexProfitRub?: number | null;
  platformFeeUsdt?: number | null;
  originalDealId?: string | null;
  replacementDealId?: string | null;
  correctionReason?: string | null;
  correctionActor?: string | null;
}

export interface ExchangeSourceEditPayload {
  exchange: {
    operationId: number;
    recvCode: string;
    recvAmount: number;
    payCode: string;
    payAmount: number;
    rate: number;
    note?: string;
  };
}

export interface CashSourceEditPayload {
  cash: {
    city: string;
    amount?: number;
    inAmount?: number;
    outAmount?: number;
    comment?: string;
    contact1?: string;
    contact2?: string;
  };
}

export type DealSourceEditPayload = ExchangeSourceEditPayload | CashSourceEditPayload;
