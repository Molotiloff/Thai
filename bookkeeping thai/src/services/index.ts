import type {
  IAccountingService,
  IAttendanceService,
  IBalancesService,
  IChartDataService,
  IClientsService,
  IDealsService,
  IExpensesService,
  IMainDashboardService,
  ITurnoverService,
  IUserService,
} from './interfaces';
import { getApiRuntimeConfig } from '@/api/config';
import { ApiClient } from '@/api/httpClient';
import { MockMainDashboardService } from './MainDashboardService';
import { MockDealsService } from './DealsService';
import { MockChartDataService } from './ChartDataService';
import { MockUserService } from './UserService';
import { MockAccountingService } from './AccountingService';
import { MockAttendanceService } from './AttendanceService';
import { ClientsService } from './ClientsService';
import { MockTurnoverService } from './TurnoverService';
import { MockExpensesService } from './ExpensesService';
import {
  ApiBalancesService,
  ApiDealsService,
  ApiMainDashboardService,
  ApiUserService,
} from './api/ApiServices';

/**
 * Composition root: единственное место, где выбираются реализации сервисов.
 *
 * Остальные экраны могут работать на mock-данных. Клиенты и балансы всегда читаются
 * из CRM API. Для подключения нужен адрес API:
 * - CRM_API_BASE_URL=http://localhost:8000/api/v1
 * Остальные экраны переключаются через CRM_USE_API=true.
 *
 * Для браузерных сценариев позже можно использовать NEXT_PUBLIC_CRM_API_BASE_URL
 * и auth-сессию вместо server-only CRM_API_TOKEN.
 */

const config = getApiRuntimeConfig();
const createApiClient = () =>
  new ApiClient({
    baseUrl: config.baseUrl,
    getHeaders: async () => {
      const { cookies } = await import('next/headers');
      const cookieHeader = (await cookies()).toString();
      return cookieHeader ? { Cookie: cookieHeader } : null;
    },
    getToken: () => process.env.CRM_API_TOKEN ?? null,
  });
const api = config.useMocks ? null : createApiClient();

export const mainDashboardService: IMainDashboardService = api
  ? new ApiMainDashboardService(api)
  : new MockMainDashboardService();
// CRM-backed screens use API implementations as their migration stages land.
// Screens outside the current backend scope keep isolated read-only mocks.
export const dealsService: IDealsService = api
  ? new ApiDealsService(api)
  : new MockDealsService();
export const chartDataService: IChartDataService = new MockChartDataService();
export const userService: IUserService = api ? new ApiUserService(api) : new MockUserService();
export const clientsService: IClientsService = new ClientsService();
export const accountingService: IAccountingService = new MockAccountingService();
export const attendanceService: IAttendanceService = new MockAttendanceService();
export const balancesService: IBalancesService = new ApiBalancesService(api ?? createApiClient());
export const turnoverService: ITurnoverService = new MockTurnoverService();
export const expensesService: IExpensesService = new MockExpensesService();
