/**
 * Доменные типы CRM SkyEx.
 * Модель повторяет флоу из ТЗ: заявка приходит из Telegram-бота
 * (crm_requests + request_legs), менеджер ведёт её по статусам,
 * при завершении фиксируются прибыль, касса и ссылка Tronscan.
 */

/** Статусы сделки из backend deal_status_events */
export type DealStatus =
  | 'new'
  | 'fixed'
  | 'balance_check'
  | 'awaiting_payment'
  | 'in_delivery'
  | 'ready_for_cash_settlement'
  | 'done'
  | 'canceled';

/** Валютное направление обмена */
export type DealDirection = 'USDT/RUB' | 'BTC/RUB' | 'ETH/RUB' | 'OTHER';

export interface Deal {
  id: string;
  clientName: string;
  /** Покупка/продажа + актив, например «Покупка USDT» */
  operation: string;
  direction: DealDirection;
  amountRub: number;
  status: DealStatus;
  /** Derived alert from act_request_transactions; not a status. */
  insufficientUsdt?: boolean;
  /** Минуты с момента последнего status_event */
  updatedMinutesAgo: number;
  /** Ссылка на транзакцию — появляется при завершении сделки */
  tronscanUrl?: string;
  /** Объём в активе, например «3 500 USDT» */
  assetAmount?: string;
  /** Зафиксированный курс сделки */
  rate?: number;
}

export interface ProfitPoint {
  /** Подпись по оси X, например «1 мая» */
  label: string;
  value: number;
}

export interface DealStructureSlice {
  direction: DealDirection;
  label: string;
  /** Доля от всех сделок, 0–100 */
  percent: number;
}

export interface DealStructure {
  totalDeals: number;
  slices: DealStructureSlice[];
}

export type CurrencyCode =
  | 'RUB'
  | 'USDT'
  | 'USD'
  | 'USD_BL'
  | 'USD_WH'
  | 'EUR'
  | 'BTC'
  | 'ETH'
  | 'CNY';

export interface MoneyAmount {
  currency: CurrencyCode;
  amount: number;
}

/** Касса города с балансами по валютам */
export interface CashDesk {
  id: string;
  city: string;
  balances: MoneyAmount[];
}

/** Перемещение средств между кассами */
export interface CashTransfer {
  id: string;
  fromCity: string;
  toCity: string;
  amount: MoneyAmount;
  date: string;
  comment?: string;
}

/** P&L за период (для экспорта PDF-отчёта) */
export interface PnLReport {
  periodLabel: string;
  clientIncome: number;
  fixedExpenses: number;
  variableExpenses: number;
  profit: number;
}

/** Роли CRM из backend RBAC */
export type UserRole = 'cashier' | 'manager' | 'accountant' | 'owner' | 'admin';

export interface CurrentUser {
  name: string;
  role: UserRole;
  roleLabel: string;
  initials: string;
}
