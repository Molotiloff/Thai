import type { IAccountingService } from './interfaces';
import type { CashDesk, CashTransfer, PnLReport } from '@/types/domain';

/** Мок-реализация бухгалтерии: кассы по городам, перемещения, P&L */
export class MockAccountingService implements IAccountingService {
  async getCashDesks(): Promise<CashDesk[]> {
    return [
      {
        id: 'd1',
        city: 'Екатеринбург',
        balances: [
          { currency: 'RUB', amount: 2_206_000 },
          { currency: 'USDT', amount: 123.1258 },
          { currency: 'EUR', amount: 56 },
        ],
      },
      {
        id: 'd2',
        city: 'Москва',
        balances: [
          { currency: 'RUB', amount: 17_815_825 },
          { currency: 'USD_WH', amount: 0 },
          { currency: 'USD_BL', amount: 0 },
        ],
      },
      {
        id: 'd3',
        city: 'Челябинск',
        balances: [
          { currency: 'RUB', amount: 30_600 },
        ],
      },
      {
        id: 'd4',
        city: 'Тюмень',
        balances: [
          { currency: 'RUB', amount: 5_025_500 },
        ],
      },
    ];
  }

  async getTransfers(): Promise<CashTransfer[]> {
    return [
      { id: 't1', fromCity: 'Екатеринбург', toCity: 'Челябинск', amount: { currency: 'RUB', amount: 8_658 }, date: '01.06.2026', comment: 'Перестановка: Ярослав П / Cassa Cassa' },
      { id: 't2', fromCity: 'Москва', toCity: 'Екатеринбург', amount: { currency: 'USDT', amount: 31_500 }, date: '01.06.2026', comment: 'Покупка у Поэты' },
      { id: 't3', fromCity: 'Екатеринбург', toCity: 'Москва', amount: { currency: 'USDT', amount: 103_654.485 }, date: '01.06.2026', comment: 'Продажа Gikk' },
      { id: 't4', fromCity: 'Москва', toCity: 'Тюмень', amount: { currency: 'RUB', amount: 5_000 }, date: '08.06.2026', comment: 'Доставка / инкасс' },
    ];
  }

  async getPnL(): Promise<PnLReport> {
    return {
      periodLabel: '01.06.2026 – 30.06.2026',
      clientIncome: 4_400_346,
      fixedExpenses: 4_301_489,
      variableExpenses: 98_857,
      profit: -1,
    };
  }
}
