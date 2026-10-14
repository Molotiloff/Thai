import type {
  CashDesk,
  CashTransfer,
  CurrentUser,
  Deal,
  DealStructure,
  PnLReport,
  ProfitPoint,
} from '@/types/domain';
import type { Client, ClientsPageData } from '@/types/clients';
import type { AttendanceMonth } from '@/types/attendance';
import type { BalancesSnapshot } from '@/types/balances';
import type { DealDetails, DealSourceEditPayload, DealsPageData } from '@/types/deals';
import type { CashDealRequest, ClientTransferRequest, ExchangeDealRequest, NewDealContext } from '@/types/newDeal';
import type { TurnoverPageData } from '@/types/turnover';
import type { DashboardShadowReport, MainDashboardData } from '@/types/mainDashboard';
import type { ExpensesPageData } from '@/types/expenses';
import type {
  ManualCashSnapshot,
  RecordManualCashPayload,
  ReverseManualCashPayload,
} from '@/types/manualCash';

/**
 * Контракты слоя данных (Dependency Inversion):
 * страницы и компоненты зависят только от этих интерфейсов.
 * Сейчас реализации отдают мок-данные, при подключении CRM API
 * достаточно заменить реализацию в composition root (services/index.ts).
 */

export interface IMainDashboardService {
  /** Все блоки страницы «Главная»: KPI, валюты, финансы, города, системные показатели */
  getDashboard(): Promise<MainDashboardData>;
  getShadowReport(reportId: number): Promise<DashboardShadowReport | null>;
  getManualCash(): Promise<ManualCashSnapshot>;
  recordManualCash(payload: RecordManualCashPayload): Promise<void>;
  reverseManualCash(moveId: number, payload: ReverseManualCashPayload): Promise<void>;
}

export interface IDealsService {
  /** Компактный список активных сделок для дашборда «Главная» */
  getActiveDeals(): Promise<Deal[]>;
  /** Полные данные страницы «Сделки»: сводка, kanban и реестр */
  getDealsPage(): Promise<DealsPageData>;
  /** Карточка сделки: шапка, legs, события статусов, чек */
  getDealById(id: string): Promise<DealDetails | null>;
  /** Справочники формы создания сделки: города, контрагенты, клиенты, курсы */
  getNewDealContext(): Promise<NewDealContext>;
  createClientTransfer(payload: ClientTransferRequest): Promise<DealDetails>;
  createExchange(payload: ExchangeDealRequest): Promise<DealDetails>;
  createCash(payload: CashDealRequest): Promise<DealDetails>;
  writeToTable(id: string): Promise<DealDetails>;
  payReferrerSpread(id: string, payload: import('@/types/referrerSpread').ReferrerSpreadPayRequest): Promise<DealDetails>;
  editSource(id: string, payload: DealSourceEditPayload): Promise<DealDetails>;
  cancel(id: string, comment?: string): Promise<DealDetails>;
}

export interface IClientsService {
  /** Клиентская база: KPI и список клиентов с полными карточками */
  getClientsPage(): Promise<ClientsPageData>;
  /** Карточка клиента: балансы, сделки, комментарии */
  getClientById(id: string): Promise<Client | null>;
  updateTelegramInviteLink(id: string, inviteLink: string | null): Promise<Client>;
}

export interface IAccountingService {
  getCashDesks(): Promise<CashDesk[]>;
  getTransfers(): Promise<CashTransfer[]>;
  getPnL(): Promise<PnLReport>;
}

export interface IChartDataService {
  getProfitDynamics(): Promise<ProfitPoint[]>;
  getDealStructure(): Promise<DealStructure>;
}

export interface IUserService {
  getCurrentUser(): Promise<CurrentUser>;
}

export interface IAttendanceService {
  getMonth(): Promise<AttendanceMonth>;
}

export interface IBalancesService {
  getSnapshot(): Promise<BalancesSnapshot>;
}

export interface ITurnoverService {
  getTurnover(): Promise<TurnoverPageData>;
}

export interface IExpensesService {
  /** Данные страницы «Расходы»: KPI и обе таблицы (постоянные/переменные) */
  getExpensesPage(): Promise<ExpensesPageData>;
}
