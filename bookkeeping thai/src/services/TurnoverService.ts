import type { ITurnoverService } from './interfaces';
import type { TurnoverDailyPoint, TurnoverPageData } from '@/types/turnover';

/** Динамика по первым дням июня из листа «Прибыль» */
function generateDaily(): TurnoverDailyPoint[] {
  const points = [
    { day: 1, sale: 145_611, deals: 136_872 },
    { day: 2, sale: 40_063, deals: 113_760 },
    { day: 3, sale: 206_493, deals: 28_559 },
    { day: 4, sale: 138_058, deals: 20_898 },
    { day: 5, sale: 155_351, deals: 14_925 },
    { day: 6, sale: 140_273, deals: 13_853 },
    { day: 7, sale: 7_317, deals: 4_100 },
  ];
  return points.map((point) => ({
    label: `${point.day} июня`,
    turnover: point.sale + point.deals,
    purchase: point.deals,
    sale: point.sale,
  }));
}

/** Мок-реализация аналитики оборота компании */
export class MockTurnoverService implements ITurnoverService {
  async getTurnover(): Promise<TurnoverPageData> {
    return {
      periodLabel: '01.06.2026 - 30.06.2026',
      cities: ['Екатеринбург', 'Челябинск', 'Москва'],
      metrics: [
        { id: 'turnover', label: 'Общая оборотка', valueText: '44 636 200 ₽', changePercent: 0, changeNote: 'лист «Оборотка»', icon: 'coins', tone: 'purple' },
        { id: 'purchase', label: 'Продажа USDT', valueText: '641 786 991 ₽', changePercent: 0, changeNote: 'лист «Статиситка»', icon: 'wallet', tone: 'blue' },
        { id: 'sale', label: 'Объём USDT', valueText: '8 377 470', changePercent: 0, changeNote: 'USDT', icon: 'trend-up', tone: 'green' },
        { id: 'profit', label: 'Доход USDT', valueText: '2 987 962 ₽', changePercent: 0, changeNote: 'по валюте', icon: 'income', tone: 'orange' },
        { id: 'spread', label: 'Средний спред', valueText: '0.47%', changePercent: 0, changeNote: 'USDT', icon: 'percent', tone: 'green' },
        { id: 'deals', label: 'Строк продаж', valueText: '3 949', changePercent: 0, changeNote: 'лист «Продажа»', icon: 'deals', tone: 'blue' },
      ],
      daily: generateDaily(),
      structure: {
        totalRub: 641_786_991,
        slices: [
          { id: 'purchase', label: 'USDT', percent: 99.8, amountRub: 641_786_991 },
          { id: 'sale', label: 'USD', percent: 0.2, amountRub: 1_528_054 },
        ],
      },
      cityRows: [
        { city: 'Екатеринбург', turnover: 319_125_257, purchase: 0, sale: 319_125_257, profit: 1_664_867, changePercent: 49.64 },
        { city: 'Челябинск', turnover: 121_470_171, purchase: 0, sale: 121_470_171, profit: 875_856, changePercent: 18.80 },
        { city: 'Москва', turnover: 175_325_012, purchase: 0, sale: 175_325_012, profit: 340_820, changePercent: 27.13 },
      ],
      currencyRows: [
        { currency: 'USDT', turnover: 641_786_991, sharePercent: 99.8 },
        { currency: 'USD', turnover: 1_528_054, sharePercent: 0.2 },
      ],
      ownerShares: [
        { ownerId: 'o-1', ownerName: 'Алексей', percent: 35.68, amount: 15_925_700 },
        { ownerId: 'o-2', ownerName: 'Влад', percent: 10.06, amount: 4_492_609 },
        { ownerId: 'o-3', ownerName: 'Бабушка', percent: 6.72, amount: 3_000_000 },
        { ownerId: 'o-4', ownerName: 'Никита', percent: 2.64, amount: 1_177_891 },
        { ownerId: 'o-5', ownerName: 'Костет', percent: 22.4, amount: 10_000_000 },
        { ownerId: 'o-6', ownerName: 'Сочи', percent: 11.29, amount: 5_040_000 },
        { ownerId: 'o-7', ownerName: 'Иван', percent: 11.2, amount: 5_000_000 },
      ],
      ownerInvestments: [
        { ownerId: 'o-1', ownerName: 'Алексей', invested: 15_925_700, currentTurnover: 15_925_700, profit: 0, payoutPercent: 0 },
        { ownerId: 'o-2', ownerName: 'Влад', invested: 4_492_609, currentTurnover: 4_492_609, profit: 0, payoutPercent: 0 },
        { ownerId: 'o-3', ownerName: 'Бабушка', invested: 3_000_000, currentTurnover: 3_000_000, profit: 60_000, payoutPercent: 2 },
        { ownerId: 'o-4', ownerName: 'Никита', invested: 1_177_891, currentTurnover: 1_177_891, profit: 29_447, payoutPercent: 2.5 },
        { ownerId: 'o-5', ownerName: 'Костет', invested: 10_000_000, currentTurnover: 10_000_000, profit: 250_000, payoutPercent: 2.5 },
        { ownerId: 'o-6', ownerName: 'Сочи', invested: 5_040_000, currentTurnover: 5_040_000, profit: 100_800, payoutPercent: 2 },
        { ownerId: 'o-7', ownerName: 'Иван', invested: 5_000_000, currentTurnover: 5_000_000, profit: 125_000, payoutPercent: 2.5 },
      ],
      ownerTransactions: [
        { id: 'ot-1', date: '18.03.2026', type: 'Вклад', ownerName: 'Алексей', amount: 17_756_700, comment: 'Строка листа «Оборотка»' },
        { id: 'ot-2', date: '18.03.2026', type: 'Вклад', ownerName: 'Никита', amount: 550_000, comment: 'Строка листа «Оборотка»' },
        { id: 'ot-3', date: '18.03.2026', type: 'Вклад', ownerName: 'Костя', amount: 10_000_000, comment: 'Строка листа «Оборотка»' },
        { id: 'ot-4', date: '26.03.2026', type: 'Вклад', ownerName: 'Влад', amount: 1_000_000, comment: 'Строка листа «Оборотка»' },
      ],
      recentDeals: [
        { id: '#S-005', clientName: 'Gikk', type: 'Продажа', direction: 'USDT → RUB', amountRub: 7_800_000, time: '01.06.2026' },
        { id: '#S-006', clientName: 'Кит Бокс', type: 'Продажа', direction: 'USDT → RUB', amountRub: 7_535_000, time: '01.06.2026' },
        { id: '#P-004', clientName: 'B Ekb', type: 'Покупка', direction: 'USDT → RUB', amountRub: 19_989_900, time: '01.06.2026' },
        { id: '#P-002', clientName: 'Поэты', type: 'Покупка', direction: 'USDT → RUB', amountRub: 2_370_375, time: '01.06.2026' },
      ],
      periodSummary: {
        maxTurnover: { value: 319_125_257, date: 'Екб' },
        minTurnover: { value: 121_470_171, date: 'Члб' },
        maxProfit: { value: 1_664_867, date: 'Екб' },
        averageDailyProfit: 423_994,
        averageCheck: 162_528,
      },
    };
  }
}
