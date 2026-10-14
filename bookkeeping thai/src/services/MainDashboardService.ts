import type { IMainDashboardService } from './interfaces';
import type { DashboardShadowReport, MainDashboardData } from '@/types/mainDashboard';
import type { ManualCashSnapshot } from '@/types/manualCash';
import { formatSignedRub, formatUsd } from '@/lib/format';

/** Мок-реализация главного дашборда: цифры — как в утверждённом референсе */
export class MockMainDashboardService implements IMainDashboardService {
  async getShadowReport(): Promise<DashboardShadowReport | null> {
    return null;
  }

  async getManualCash(): Promise<ManualCashSnapshot> {
    return { accounts: [], moves: [] };
  }

  async recordManualCash(): Promise<void> {}

  async reverseManualCash(): Promise<void> {}

  async getDashboard(): Promise<MainDashboardData> {
    return {
      source: 'mock',
      calculatedAt: '2026-07-03T00:00:00Z',
      dataAsOf: '2026-07-03T00:00:00Z',
      warnings: [],
      dateLabel: '03.07.2026',
      weekdayLabel: 'Пятница',
      cities: ['Екатеринбург', 'Челябинск', 'Москва', 'Тюмень'],
      topMetrics: [
        { id: 'fact-turnover', title: 'Факт. оборот', value: '44 636 242 ₽', subtitleLabel: 'Оборот', subtitleValue: '44 636 200 ₽', tone: 'blue', icon: 'reports' },
        { id: 'income-today', title: 'Доход', value: '4 400 346 ₽', subtitleLabel: 'Общий RUB', subtitleValue: '25 092 901 ₽', tone: 'green', icon: 'trend-up' },
        { id: 'profit-today', title: 'Общая прибыль', value: '-0,49 ₽', subtitleLabel: 'Разрыв', subtitleValue: '42,67 ₽', tone: 'red', icon: 'coins' },
        { id: 'expense-today', title: 'Расход', value: '4 400 346 ₽', subtitleLabel: 'Балансы клиентов', subtitleValue: '-13 157 376 ₽', tone: 'red', icon: 'expenses' },
      ],
      dailyIndicators: [
        { id: 'income', label: 'Доход', value: '4 400 346 ₽', tone: 'green' },
        { id: 'expense', label: 'Расход', value: '4 400 346 ₽', tone: 'red' },
        { id: 'profit', label: 'Общая прибыль', value: '-0,49 ₽', tone: 'red' },
        { id: 'turnover', label: 'Оборот', value: '44 636 200 ₽', tone: 'blue' },
        { id: 'gap', label: 'Разрыв', value: formatSignedRub(43), tone: 'orange' },
      ],
      currencies: [
        { code: 'EUR', label: 'EUR', amount: 56, rate: 88.514, rubValue: 4_957, clientAmount: 644, dealProfitAmount: 0, factAmount: 700, tone: 'blue' },
        { code: 'USDT', label: 'USDT', amount: 123.1258, rate: 81.377, rubValue: 10_020, clientAmount: 18_843.22, dealProfitAmount: 0, factAmount: 18_966.35, tone: 'green' },
        { code: 'USD_WH', label: 'USD WH', amount: 0, rate: 0, rubValue: 0, clientAmount: 0, dealProfitAmount: 0, factAmount: 0, tone: 'orange' },
        { code: 'USD_BL', label: 'USD BL', amount: 0, rate: 0, rubValue: 0, clientAmount: 0, dealProfitAmount: 0, factAmount: 0, tone: 'purple' },
      ],
      finance: [
        { id: 'total-rub', label: 'Общий RUB', value: '25 092 901 ₽' },
        { id: 'total-balances', label: 'Общий балансы', value: '-19 543 341 ₽', isNegative: true },
        { id: 'fact-rub', label: 'Факт RUB', value: '5 549 560 ₽' },
        { id: 'rub-in-currency', label: 'RUB в валюте', value: '10 020 ₽', percent: '0,02%' },
        { id: 'client-balances', label: 'Балансы клиентов', value: '-13 157 376 ₽', isNegative: true },
        { id: 'skyex-balances', label: 'Балансы SkyEx', value: '-6 385 965 ₽', isNegative: true },
      ],
      citySummaries: [
        {
          id: 'ekb',
          city: 'ЕКБ',
          rows: [
            { label: 'Доход Екб', value: '1 683 227 ₽', tone: 'green' },
            { label: 'Расход Екб', value: '0 ₽' },
            { label: 'Прибыль Екб', value: '1 683 227 ₽', tone: 'green' },
          ],
        },
        {
          id: 'chlb',
          city: 'ЧЛБ',
          rows: [
            { label: 'Доход Члб', value: '875 856 ₽', tone: 'green' },
            { label: 'Расход Члб', value: '0 ₽' },
            { label: 'Прибыль Члб', value: '875 856 ₽', tone: 'green' },
          ],
        },
        {
          id: 'msk',
          city: 'МСК',
          rows: [
            { label: 'Прибыль Мск', value: '340 816 ₽', tone: 'green' },
            { label: 'Приб. с городов', value: '2 899 899 ₽', tone: 'green' },
            { label: 'Приб. со сделок', value: '1 394 028 ₽', tone: 'green' },
            { label: 'Общая', value: '4 400 346 ₽', tone: 'green' },
          ],
        },
      ],
      systemMetrics: [
        { id: 'yield', title: 'Доходность', value: '0,69%', subtitle: 'Доход / продажа USDT', tone: 'green' },
        { id: 'month-turnover', title: 'Месячный оборот', value: '641 786 991 ₽', subtitle: 'Продажа USDT за период', tone: 'blue' },
        { id: 'usdt-rest', title: 'Свободные USDT', value: formatUsd(1), subtitle: 'Строка «Свободные»', tone: 'green' },
        { id: 'gap', title: 'Разрыв', value: formatSignedRub(43), subtitle: 'Факт. оборот − оборот', tone: 'orange' },
        { id: 'working-capital', title: 'Оборотка', value: '44 636 200 ₽', subtitle: 'Лист «Оборотка»', tone: 'purple' },
        { id: 'rub', title: 'RUB', value: '25 077 925 ₽', subtitle: 'Доля от оборота 56,18%', tone: 'blue' },
        { id: 'rub-in-currency', title: 'RUB в валюте', value: '10 020 ₽', subtitle: 'USDT позиция фирмы', tone: 'orange' },
      ],
      lastUpdatedLabel: 'Google Sheets export: 03.07.2026',
    };
  }
}
