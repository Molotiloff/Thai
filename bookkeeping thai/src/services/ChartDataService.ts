import type { IChartDataService } from './interfaces';
import type { DealStructure, ProfitPoint } from '@/types/domain';

/** Мок-реализация данных аналитики за период 01.06–30.06 из Google Sheets */
export class MockChartDataService implements IChartDataService {
  async getProfitDynamics(): Promise<ProfitPoint[]> {
    const points = [
      ['1 июня', 277_463],
      ['2 июня', 112_843],
      ['3 июня', 232_908],
      ['4 июня', 158_531],
      ['5 июня', 161_276],
      ['6 июня', 151_126],
      ['7 июня', 7_917],
    ] as const;
    return points.map(([label, value]) => ({ label, value }));
  }

  async getDealStructure(): Promise<DealStructure> {
    return {
      totalDeals: 5_929,
      slices: [
        { direction: 'USDT/RUB', label: 'USDT / RUB', percent: 99.8 },
        { direction: 'OTHER', label: 'USD / RUB', percent: 0.2 },
        { direction: 'BTC/RUB', label: 'BTC / RUB', percent: 0 },
        { direction: 'ETH/RUB', label: 'ETH / RUB', percent: 0 },
      ],
    };
  }
}
