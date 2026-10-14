import type { IExpensesService } from './interfaces';
import type { ExpensesPageData } from '@/types/expenses';

/** Мок-реализация расходов: структура и строки — из таблицы Google Sheets */
export class MockExpensesService implements IExpensesService {
  async getExpensesPage(): Promise<ExpensesPageData> {
    return {
      periodLabel: '01.06.2026 - 30.06.2026',
      cities: ['Екатеринбург', 'Челябинск', 'Тюмень', 'Москва'],
      metrics: [
        { id: 'total', title: 'Всего расходов', value: 4_400_346, tone: 'blue', icon: 'wallet' },
        { id: 'fixed', title: 'Постоянные расходы', value: 4_301_489, tone: 'orange', icon: 'building' },
        { id: 'variable', title: 'Переменные расходы', value: 98_857, tone: 'green', icon: 'trend-up' },
        { id: 'operational', title: 'Расходы за день', value: 5_020, tone: 'purple', icon: 'settings' },
        { id: 'other', title: 'TRX', value: 53_339, tone: 'gray', icon: 'file-text' },
      ],
      fixedExpenses: [
        { id: 'fe-1', category: 'Аренда', amount: 30_000, date: '02.06.2026', comment: 'офис в члб', city: 'Челябинск' },
        { id: 'fe-2', category: 'Бензин', amount: 3_000, date: '02.06.2026', comment: 'Никита', city: 'Екатеринбург' },
        { id: 'fe-3', category: 'Доставка', amount: 1_000, date: '03.06.2026', comment: 'саша члб', city: 'Челябинск' },
        { id: 'fe-4', category: 'Такси', amount: 425, date: '04.06.2026', comment: 'Лев', city: 'Екатеринбург' },
        { id: 'fe-5', category: 'Инкас члб', amount: 3_000, date: '05.06.2026', comment: 'Саша члб', city: 'Челябинск' },
        { id: 'fe-6', category: 'Интернет', amount: 3_000, date: '05.06.2026', comment: 'Саша члб', city: 'Челябинск' },
        { id: 'fe-7', category: 'Инкас члб', amount: 3_000, date: '05.06.2026', comment: 'Сергей', city: 'Челябинск' },
        { id: 'fe-8', category: 'Доставка', amount: 3_000, date: '06.06.2026', comment: 'саша члб', city: 'Челябинск' },
        { id: 'fe-9', category: 'Доставка', amount: 3_500, date: '07.06.2026', comment: 'саша члб', city: 'Челябинск' },
        { id: 'fe-10', category: 'Доставка', amount: 5_000, date: '08.06.2026', comment: 'инкасс', city: 'Тюмень' },
        { id: 'fe-11', category: 'Аренда', amount: 35_000, date: '08.06.2026', comment: 'кв Миша', city: 'Тюмень' },
        { id: 'fe-12', category: 'Доставка', amount: 5_000, date: '09.06.2026', comment: '4ех', city: 'Тюмень' },
      ],
      variableExpenses: [
        { id: 've-1', name: 'Пропуска в офис Никита', amount: 2_184, date: '01.06.2026', city: 'Екатеринбург' },
        { id: 've-2', name: 'Пропуск в фоис Никита', amount: 504, date: '01.06.2026', city: 'Екатеринбург' },
        { id: 've-3', name: 'продали меньше тезера клиент от Влада', amount: 2_332, date: '01.06.2026', city: null },
        { id: 've-4', name: 'Прибыль K ekb с B екб 0,03', amount: 7_980, date: '02.06.2026', city: 'Екатеринбург' },
        { id: 've-5', name: 'За сервера', amount: 1_144, date: '03.06.2026', city: null },
        { id: 've-6', name: 'Др Данилы', amount: 26_608, date: '08.06.2026', city: null },
        { id: 've-7', name: 'Др Никиты', amount: 30_000, date: '15.06.2026', city: null },
        { id: 've-8', name: 'флешка', amount: 1_000, date: '24.06.2026', city: null },
        { id: 've-9', name: 'посидели в кафе Влад', amount: 13_000, date: '29.06.2026', city: null },
        { id: 've-10', name: 'Анита, Сергей', amount: 5_115, date: '29.06.2026', city: 'Екатеринбург' },
        { id: 've-11', name: 'Эстонский номер для Вани', amount: 8_990, date: '30.06.2026', city: null },
      ],
      moreFixedCount: 18,
      moreVariableCount: 0,
      totalCount: 74,
    };
  }
}
