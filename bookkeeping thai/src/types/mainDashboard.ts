/**
 * Типы страницы «Главная»: быстрые финансовые показатели дня,
 * фактическое количество валют (как в Google Sheets), финансы,
 * показатели по городам и системные показатели. Без графиков —
 * аналитика прибыли живёт в «Бухгалтерии»/«Отчётах».
 */

export type DashboardCity = 'Все города' | 'Екатеринбург' | 'Челябинск' | 'Москва';

export type DashboardCurrencyCode = 'EUR' | 'USDT' | 'USD_WH' | 'USD_BL';

export type MetricTone = 'blue' | 'green' | 'red' | 'orange' | 'purple' | 'neutral';

export interface TopMetric {
  id: string;
  title: string;
  value: string | number;
  subtitleLabel?: string;
  subtitleValue?: string | number;
  tone: MetricTone;
  icon: import('@/components/ui/Icon').IconName;
}

export interface DailyIndicator {
  id: string;
  label: string;
  value: string | number;
  tone: MetricTone;
}

export interface CurrencyFactCard {
  code: DashboardCurrencyCode;
  label: string;
  amount: number;
  rate: number;
  rubValue: number;
  clientAmount: number;
  dealProfitAmount: number;
  factAmount: number;
  observedAmount?: number;
  gap?: number;
  observedAt?: string;
  tone: MetricTone;
}

export interface FinanceIndicator {
  id: string;
  label: string;
  value: string | number;
  percent?: string;
  isNegative?: boolean;
}

export interface CitySummary {
  id: string;
  city: string;
  rows: {
    label: string;
    value: string | number;
    tone?: MetricTone;
  }[];
}

export interface SystemMetric {
  id: string;
  title: string;
  value: string | number;
  subtitle: string;
  tone: MetricTone;
}

export interface DashboardShadowComparison {
  status: 'matched' | 'mismatched' | 'unavailable';
  comparedFields: number;
  mismatchCount: number;
  reportId?: number;
}

export interface DashboardShadowDifference {
  path: string;
  sheet?: string;
  database?: string;
  absoluteDelta?: string;
  relativeDelta?: string;
  classification: string;
}

export interface DashboardShadowReport {
  id: number;
  businessDate: string;
  primarySource: string;
  status: string;
  comparedFields: number;
  mismatchCount: number;
  absoluteTolerance: string;
  relativeTolerance: string;
  sheetsDataAsOf: string;
  dbDataAsOf: string;
  createdAt: string;
  differences: DashboardShadowDifference[];
}

export interface MainDashboardData {
  source: string;
  calculatedAt: string;
  dataAsOf: string;
  warnings: string[];
  shadowComparison?: DashboardShadowComparison;
  dateLabel: string;
  weekdayLabel: string;
  cities: string[];
  topMetrics: TopMetric[];
  dailyIndicators: DailyIndicator[];
  currencies: CurrencyFactCard[];
  finance: FinanceIndicator[];
  citySummaries: CitySummary[];
  systemMetrics: SystemMetric[];
  lastUpdatedLabel: string;
}
