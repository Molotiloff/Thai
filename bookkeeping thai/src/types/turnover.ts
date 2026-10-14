import type { IconName } from '@/components/ui/Icon';

/**
 * Типы страницы «Оборот»: KPI, динамика и структура оборота,
 * разрезы по городам/валютам, вложения владельцев и сводка за период.
 */

export interface TurnoverMetric {
  id: string;
  label: string;
  /** Готовое к показу значение: «12 450 000 ₽», «1.25%», «342» */
  valueText: string;
  changePercent: number;
  /** Подпись к дельте, например «к апрелю» */
  changeNote: string;
  icon: IconName;
  tone: 'purple' | 'blue' | 'green' | 'orange';
}

export interface TurnoverDailyPoint {
  /** Подпись по оси X, например «15 мая» */
  label: string;
  turnover: number;
  purchase: number;
  sale: number;
}

export interface TurnoverStructureSlice {
  id: 'purchase' | 'sale';
  label: string;
  percent: number;
  amountRub: number;
}

export interface CityTurnoverRow {
  city: string;
  turnover: number;
  purchase: number;
  sale: number;
  profit: number;
  changePercent: number;
}

export interface CurrencyTurnoverRow {
  currency: string;
  turnover: number;
  /** Доля в общем обороте, % */
  sharePercent: number;
}

export interface OwnerInvestmentShare {
  ownerId: string;
  ownerName: string;
  amount: number;
  percent: number;
}

export interface OwnerInvestment {
  ownerId: string;
  ownerName: string;
  invested: number;
  currentTurnover: number;
  profit: number;
  /** Динамический столбик «% выплаты» */
  payoutPercent: number;
}

export type OwnerTransactionType = 'Вклад' | 'Вывод';

export interface OwnerTransaction {
  id: string;
  date: string;
  type: OwnerTransactionType;
  ownerName: string;
  amount: number;
  comment?: string;
}

export interface RecentDeal {
  id: string;
  clientName: string;
  type: 'Продажа' | 'Покупка';
  direction: string;
  amountRub: number;
  time: string;
}

export interface PeriodSummary {
  maxTurnover: { value: number; date: string };
  minTurnover: { value: number; date: string };
  maxProfit: { value: number; date: string };
  averageDailyProfit: number;
  averageCheck: number;
}

export interface TurnoverPageData {
  periodLabel: string;
  cities: string[];
  metrics: TurnoverMetric[];
  daily: TurnoverDailyPoint[];
  structure: { totalRub: number; slices: TurnoverStructureSlice[] };
  cityRows: CityTurnoverRow[];
  currencyRows: CurrencyTurnoverRow[];
  ownerShares: OwnerInvestmentShare[];
  ownerInvestments: OwnerInvestment[];
  ownerTransactions: OwnerTransaction[];
  recentDeals: RecentDeal[];
  periodSummary: PeriodSummary;
}
