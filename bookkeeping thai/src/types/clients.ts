import type { CurrencyCode } from './balances';

/**
 * Типы страницы «Клиенты»: клиентская база с Telegram-связкой,
 * балансами, статистикой, историей сделок и комментариями.
 * Карточка клиента создаётся ботом при добавлении в чат или вручную.
 */

export interface ClientBalance {
  currency: CurrencyCode;
  amount: number;
  unvaluedAmount?: number;
}

export interface ClientComment {
  id: string;
  date: string;
  author: string;
  text: string;
}

export interface ClientRecentDeal {
  id: string;
  type: 'Продажа' | 'Покупка' | 'Конвертация' | 'Инвойс' | 'Пополнение' | 'Списание' | 'BestChange' | 'Перевод';
  direction: string;
  amountUsdt: number;
  time: string;
  href?: string;
  status?: string;
}

export interface Client {
  id: string;
  clientNumber: string;
  /** По умолчанию — название Telegram-чата, можно изменить вручную */
  name: string;
  initials: string;
  telegramUsername: string;
  telegramChatId: string;
  telegramInviteLink?: string | null;
  dealsCount: number;
  turnoverUsdt: number;
  managerName: string;
  registrationDate: string;
  counterpartyName?: string;
  counterpartyPercent?: number;
  comment?: string;
  balances: ClientBalance[];
  totalProfitUsdt: number;
  purchaseVolumeUsdt: number;
  saleVolumeUsdt: number;
  averageCheckUsdt: number;
  recentDeals: ClientRecentDeal[];
  comments: ClientComment[];
  groupCode?: string;
  groupName?: string;
  isActive?: boolean;
}

export interface ClientMetric {
  id: string;
  title: string;
  value: string | number;
  changePercent: number;
  subtitle: string;
  tone: 'blue' | 'green' | 'orange' | 'purple';
  icon: import('@/components/ui/Icon').IconName;
}

export interface ClientsPageData {
  metrics: ClientMetric[];
  clients: Client[];
  /** Общее число клиентов в базе — для пагинации */
  totalClients: number;
}
