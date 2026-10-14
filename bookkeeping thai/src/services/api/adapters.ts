import type { components } from '@/api/openapi';
import type { IconName } from '@/components/ui/Icon';
import type { BalancesSnapshot, CurrencyCode } from '@/types/balances';
import type { Client, ClientsPageData } from '@/types/clients';
import type { CurrentUser, UserRole } from '@/types/domain';
import type {
  DashboardCurrencyCode,
  MainDashboardData,
  MetricTone,
} from '@/types/mainDashboard';

type Schemas = components['schemas'];

const DASHBOARD_CURRENCY_CODES = [
  'EUR',
  'USDT',
  'USD_WH',
  'USD_BL',
] as const satisfies readonly DashboardCurrencyCode[];
const METRIC_TONES = [
  'blue',
  'green',
  'red',
  'orange',
  'purple',
  'neutral',
] as const satisfies readonly MetricTone[];
const ICON_NAMES = [
  'clients',
  'check-circle',
  'plus',
  'deals',
  'coins',
  'reports',
  'expenses',
] as const satisfies readonly IconName[];
const USER_ROLES = [
  'cashier',
  'manager',
  'accountant',
  'owner',
  'admin',
] as const satisfies readonly UserRole[];
const CLIENT_DEAL_TYPES = [
  'Продажа',
  'Покупка',
  'Конвертация',
  'Инвойс',
  'Пополнение',
  'Списание',
] as const;

export type ApiClientDetails = Schemas['ClientDto'] & { telegramInviteLink?: string | null };
export type ApiClientsPage = Omit<Schemas['ClientsPageResponse'], 'clients'> & {
  clients: ApiClientDetails[];
};
export type ApiBalancesSnapshot = Omit<Schemas['BalancesSnapshotResponse'], 'clients'> & {
  clients: (Schemas['BalanceClientDto'] & { telegramInviteLink?: string | null })[];
};
export type ApiDashboard = Schemas['DashboardResponse'];
export type ApiUser = Schemas['ApiUser'];
export type TelegramLoginResponse = Schemas['LoginResponse'];

export function adaptClientsPage(data: ApiClientsPage): ClientsPageData {
  return {
    totalClients: data.totalClients,
    metrics: data.metrics.map((metric) => ({
      ...metric,
      tone: expectOneOf(metric.tone, ['blue', 'green', 'orange', 'purple'], 'client metric tone'),
      icon: expectOneOf(metric.icon, ICON_NAMES, 'client metric icon'),
    })),
    clients: data.clients.map(adaptClient),
  };
}

export function adaptClient(data: ApiClientDetails): Client {
  return {
    ...data,
    turnoverUsdt: data.turnoverRub,
    purchaseVolumeUsdt: data.purchaseVolumeRub,
    saleVolumeUsdt: data.saleVolumeRub,
    averageCheckUsdt: data.averageCheckRub,
    totalProfitUsdt: data.totalProfitRub,
    balances: (data.balances ?? []).map((balance) => ({
      ...balance,
      currency: expectNonEmpty(balance.currency, 'client balance currency'),
    })),
    recentDeals: (data.recentDeals ?? []).map((deal) => ({
      ...deal,
      amountUsdt: deal.amountRub,
      type: expectOneOf(deal.type, CLIENT_DEAL_TYPES, 'client transaction type'),
    })),
    comments: data.comments ?? [],
    counterpartyName: data.counterpartyName ?? undefined,
    counterpartyPercent: data.counterpartyPercent ?? undefined,
    comment: data.comment ?? undefined,
  };
}

export function adaptBalancesSnapshot(data: ApiBalancesSnapshot): BalancesSnapshot {
  return {
    clients: data.clients.map((client) => ({
      ...client,
      currency: expectNonEmpty(client.currency, 'balance currency'),
      telegramChatId: client.telegramChatId ?? undefined,
    })),
    summaries: data.summaries.map((summary) => ({
      ...summary,
      code:
        summary.code === 'ALL'
          ? 'ALL'
          : expectNonEmpty(summary.code, 'summary currency'),
      tone: expectOneOf(summary.tone, ['green', 'red', 'neutral'], 'summary tone'),
      changePercent: summary.changePercent ?? undefined,
    })),
  };
}

export function adaptDashboard(data: ApiDashboard): MainDashboardData {
  return {
    ...data,
    shadowComparison: data.shadowComparison
      ? {
          ...data.shadowComparison,
          status: expectOneOf(
            data.shadowComparison.status,
            ['matched', 'mismatched', 'unavailable'],
            'dashboard shadow status',
          ),
          reportId: data.shadowComparison.reportId ?? undefined,
        }
      : undefined,
    topMetrics: data.topMetrics.map((metric) => ({
      ...metric,
      subtitleLabel: metric.subtitleLabel ?? undefined,
      subtitleValue: metric.subtitleValue ?? undefined,
      tone: expectOneOf(metric.tone, METRIC_TONES, 'dashboard metric tone'),
      icon: expectOneOf(metric.icon, ICON_NAMES, 'dashboard metric icon'),
    })),
    dailyIndicators: data.dailyIndicators.map((indicator) => ({
      ...indicator,
      tone: expectOneOf(indicator.tone, METRIC_TONES, 'daily indicator tone'),
    })),
    currencies: data.currencies.map((currency) => ({
      ...currency,
      code: expectOneOf(currency.code, DASHBOARD_CURRENCY_CODES, 'dashboard currency'),
      amount: Number(currency.amount),
      rate: Number(currency.rate),
      rubValue: Number(currency.rubValue),
      clientAmount: Number(currency.clientAmount),
      dealProfitAmount: Number(currency.dealProfitAmount),
      factAmount: Number(currency.factAmount),
      observedAmount:
        currency.observedAmount === null ? undefined : Number(currency.observedAmount),
      gap: currency.gap === null ? undefined : Number(currency.gap),
      observedAt: currency.observedAt ?? undefined,
      tone: expectOneOf(currency.tone, METRIC_TONES, 'currency tone'),
    })),
    finance: data.finance.map((indicator) => ({
      ...indicator,
      percent: indicator.percent ?? undefined,
      isNegative: indicator.isNegative ?? undefined,
    })),
    citySummaries: data.citySummaries.map((summary) => ({
      ...summary,
      rows: summary.rows.map((row) => ({
        ...row,
        tone: row.tone
          ? expectOneOf(row.tone, METRIC_TONES, 'city summary tone')
          : undefined,
      })),
    })),
    systemMetrics: data.systemMetrics.map((metric) => ({
      ...metric,
      tone: expectOneOf(metric.tone, METRIC_TONES, 'system metric tone'),
    })),
  };
}

export function adaptUser(data: ApiUser): CurrentUser {
  const role = expectOneOf(data.role, USER_ROLES, 'user role');
  return {
    name: data.display_name,
    role,
    roleLabel: {
      cashier: 'Кассир',
      manager: 'Менеджер',
      accountant: 'Бухгалтер',
      owner: 'Владелец',
      admin: 'Администратор',
    }[role],
    initials: initials(data.display_name),
  };
}

function expectOneOf<const T extends string>(
  value: string,
  allowed: readonly T[],
  field: string,
): T {
  if ((allowed as readonly string[]).includes(value)) return value as T;
  throw new Error(`Unexpected ${field}: ${value}`);
}

function expectNonEmpty(value: string, field: string): CurrencyCode {
  const normalized = value.trim();
  if (normalized) return normalized;
  throw new Error(`Empty ${field}`);
}

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  return words.slice(0, 2).map((word) => word[0]).join('').toUpperCase();
}
