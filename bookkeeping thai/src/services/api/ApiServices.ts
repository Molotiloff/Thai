import type { ApiClient } from '@/api/httpClient';
import { crmApi } from '@/api/endpoints';
import type {
  IAccountingService,
  IAttendanceService,
  IBalancesService,
  IChartDataService,
  IDealsService,
  IExpensesService,
  IMainDashboardService,
  ITurnoverService,
  IUserService,
} from '../interfaces';
import type {
  CashDesk,
  CashTransfer,
  CurrentUser,
  Deal,
  DealStructure,
  PnLReport,
  ProfitPoint,
} from '@/types/domain';
import type { AttendanceMonth } from '@/types/attendance';
import type { BalancesSnapshot } from '@/types/balances';
import type { DealDetails, DealSourceEditPayload, DealsPageData } from '@/types/deals';
import type { ExpensesPageData } from '@/types/expenses';
import type {
  ManualCashSnapshot,
  RecordManualCashPayload,
  ReverseManualCashPayload,
} from '@/types/manualCash';
import type { DashboardShadowReport, MainDashboardData } from '@/types/mainDashboard';
import type { CashDealRequest, ClientTransferRequest, ExchangeDealRequest, NewDealContext } from '@/types/newDeal';
import type { TurnoverPageData } from '@/types/turnover';
import {
  adaptBalancesSnapshot,
  adaptDashboard,
  adaptUser,
  type ApiBalancesSnapshot,
  type ApiDashboard,
  type ApiUser,
} from './adapters';

/**
 * API implementations are intentionally thin. During the transition from mocks
 * to FastAPI they preserve the current page DTO contracts, while the backend
 * becomes the source of truth for data and commands.
 */

export class ApiMainDashboardService implements IMainDashboardService {
  constructor(private readonly api: ApiClient) {}

  async getDashboard(): Promise<MainDashboardData> {
    return adaptDashboard(await this.api.get<ApiDashboard>(crmApi.dashboard.root));
  }

  getShadowReport(reportId: number): Promise<DashboardShadowReport> {
    return this.api.get(crmApi.dashboard.shadowReport(reportId));
  }

  getManualCash(): Promise<ManualCashSnapshot> {
    return this.api.get(crmApi.dashboard.manualCash);
  }

  async recordManualCash(payload: RecordManualCashPayload): Promise<void> {
    await this.api.post(crmApi.dashboard.manualCashMoves, payload);
  }

  async reverseManualCash(moveId: number, payload: ReverseManualCashPayload): Promise<void> {
    await this.api.post(crmApi.dashboard.reverseManualCash(moveId), payload);
  }
}

export class ApiDealsService implements IDealsService {
  constructor(private readonly api: ApiClient) {}

  getActiveDeals(): Promise<Deal[]> {
    return this.api.get(crmApi.deals.list({ status: ['new', 'fixed', 'balance_check', 'awaiting_payment', 'in_delivery'] }));
  }

  getDealsPage(): Promise<DealsPageData> {
    return this.api.get(crmApi.deals.list());
  }

  getDealById(id: string): Promise<DealDetails | null> {
    return this.api.get(crmApi.deals.byId(id));
  }

  getNewDealContext(): Promise<NewDealContext> {
    return this.api.get(crmApi.deals.schema);
  }

  createClientTransfer(payload: ClientTransferRequest): Promise<DealDetails> {
    return this.api.post(crmApi.deals.clientTransfers, payload);
  }

  createExchange(payload: ExchangeDealRequest): Promise<DealDetails> {
    return this.api.post(crmApi.deals.exchanges, payload);
  }

  createCash(payload: CashDealRequest): Promise<DealDetails> {
    return this.api.post(crmApi.deals.cash, payload);
  }

  writeToTable(id: string): Promise<DealDetails> {
    return this.api.post(crmApi.deals.table(id), {});
  }

  payReferrerSpread(id: string, payload: import('@/types/referrerSpread').ReferrerSpreadPayRequest): Promise<DealDetails> {
    return this.api.post(crmApi.deals.referrerSpread(id), payload);
  }

  editSource(id: string, payload: DealSourceEditPayload): Promise<DealDetails> {
    return this.api.patch(crmApi.deals.source(id), payload);
  }

  cancel(id: string, comment?: string): Promise<DealDetails> {
    return this.api.post(crmApi.deals.cancel(id), { comment: comment || null });
  }
}

export class ApiAccountingService implements IAccountingService {
  constructor(private readonly api: ApiClient) {}

  getCashDesks(): Promise<CashDesk[]> {
    return this.api.get(crmApi.accounting.desks);
  }

  getTransfers(): Promise<CashTransfer[]> {
    return this.api.get(crmApi.accounting.moves);
  }

  getPnL(): Promise<PnLReport> {
    return this.api.get(crmApi.accounting.pnl());
  }
}

export class ApiChartDataService implements IChartDataService {
  constructor(private readonly api: ApiClient) {}

  getProfitDynamics(): Promise<ProfitPoint[]> {
    return this.api.get(crmApi.accounting.statistics({ view: 'profit_dynamics' }));
  }

  getDealStructure(): Promise<DealStructure> {
    return this.api.get(crmApi.accounting.statistics({ view: 'deal_structure' }));
  }
}

export class ApiUserService implements IUserService {
  constructor(private readonly api: ApiClient) {}

  async getCurrentUser(): Promise<CurrentUser> {
    return adaptUser(await this.api.get<ApiUser>(crmApi.auth.me));
  }
}

export class ApiAttendanceService implements IAttendanceService {
  constructor(private readonly api: ApiClient) {}

  getMonth(): Promise<AttendanceMonth> {
    return this.api.get(crmApi.attendance.month());
  }
}

export class ApiBalancesService implements IBalancesService {
  constructor(private readonly api: ApiClient) {}

  async getSnapshot(): Promise<BalancesSnapshot> {
    return adaptBalancesSnapshot(
      await this.api.get<ApiBalancesSnapshot>(crmApi.balances.list()),
    );
  }
}

export class ApiTurnoverService implements ITurnoverService {
  constructor(private readonly api: ApiClient) {}

  getTurnover(): Promise<TurnoverPageData> {
    return this.api.get(crmApi.turnover.root);
  }
}

export class ApiExpensesService implements IExpensesService {
  constructor(private readonly api: ApiClient) {}

  getExpensesPage(): Promise<ExpensesPageData> {
    return this.api.get(crmApi.expenses.list());
  }
}
