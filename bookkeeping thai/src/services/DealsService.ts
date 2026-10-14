import type { IDealsService } from './interfaces';
import type { Deal } from '@/types/domain';
import type { DealDetails, DealItem, DealSourceEditPayload, DealsPageData } from '@/types/deals';
import type { CashDealRequest, ClientTransferRequest, ExchangeDealRequest, NewDealContext } from '@/types/newDeal';

/** Сделки kanban-доски и первой страницы реестра — как в утверждённом референсе */
const REFERENCE_DEALS: DealItem[] = [
  { id: '#S-001', clientName: 'от Саши', clientShortName: 'от Саши', dealType: 'Продажа', asset: 'USDT', amountRub: 173_771, city: 'Челябинск', status: 'new', updatedLabel: '01.06.2026', createdBy: 'Google Sheet', onKanban: true },
  { id: '#S-002', clientName: 'TAJO', clientShortName: 'TAJO', dealType: 'Продажа', asset: 'USDT', amountRub: 121_364, city: 'Челябинск', status: 'fixed', updatedLabel: '01.06.2026', createdBy: 'Google Sheet', onKanban: true },
  { id: '#S-003', clientName: 'Blato', clientShortName: 'Blato', dealType: 'Продажа', asset: 'USDT', amountRub: 2_390_505, city: 'Екатеринбург', status: 'balance_check', insufficientUsdt: true, updatedLabel: '01.06.2026', createdBy: 'Google Sheet', onKanban: true },
  { id: '#S-004', clientName: 'BestChange', clientShortName: 'BestChange', dealType: 'Продажа', asset: 'USDT', amountRub: 84_166, city: 'Челябинск', status: 'awaiting_payment', updatedLabel: '01.06.2026', createdBy: 'Google Sheet', onKanban: true },
  { id: '#S-005', clientName: 'Gikk', clientShortName: 'Gikk', dealType: 'Продажа', asset: 'USDT', amountRub: 7_800_000, city: 'Москва', status: 'done', updatedLabel: '01.06.2026', createdBy: 'Google Sheet', onKanban: true },
  { id: '#S-006', clientName: 'Кит Бокс', clientShortName: 'Кит Бокс', dealType: 'Продажа', asset: 'USDT', amountRub: 7_535_000, city: 'Екатеринбург', status: 'done', updatedLabel: '01.06.2026', createdBy: 'Google Sheet', onKanban: true },
  { id: '#P-001', clientName: 'Саша члб', clientShortName: 'Саша члб', dealType: 'Покупка', asset: 'USDT', amountRub: 69_375, city: 'Челябинск', status: 'fixed', updatedLabel: '01.06.2026', createdBy: 'Google Sheet', onKanban: true },
  { id: '#P-002', clientName: 'Поэты', clientShortName: 'Поэты', dealType: 'Покупка', asset: 'USDT', amountRub: 2_370_375, city: 'Москва', status: 'awaiting_payment', updatedLabel: '01.06.2026', createdBy: 'Google Sheet', onKanban: true },
  { id: '#P-003', clientName: 'Андрей PE', clientShortName: 'Андрей PE', dealType: 'Покупка', asset: 'USDT', amountRub: 280_900, city: 'Екатеринбург', status: 'balance_check', updatedLabel: '01.06.2026', createdBy: 'Google Sheet', onKanban: true },
  { id: '#P-004', clientName: 'B Ekb', clientShortName: 'B Ekb', dealType: 'Покупка', asset: 'USDT', amountRub: 19_989_900, city: 'Екатеринбург', status: 'in_delivery', updatedLabel: '01.06.2026', createdBy: 'Google Sheet', onKanban: true },
  { id: '#D-005', clientName: 'Ярослав П', clientShortName: 'Ярослав П', dealType: 'Покупка', asset: 'USDT', amountRub: 8_658, city: 'Другой город', status: 'canceled', updatedLabel: '01.06.2026', createdBy: 'Google Sheet', onKanban: true },
];

/** Детерминированная генерация «хвоста» реестра до 50 сделок */
function generateArchiveDeals(count: number): DealItem[] {
  const clients: [string, string][] = [
    ['Алексей Смирнов', 'Алексей С.'],
    ['Мария Кузнецова', 'Мария К.'],
    ['Дмитрий Волков', 'Дмитрий В.'],
    ['Олег Лебедев', 'Олег Л.'],
    ['Анна Лазарева', 'Анна Л.'],
    ['Сергей Волынец', 'Сергей В.'],
    ['Павел Соколов', 'Павел С.'],
    ['Игорь Новиков', 'Игорь Н.'],
    ['Ольга Титова', 'Ольга Т.'],
  ];
  const cities = ['Екатеринбург', 'Москва', 'Казань', 'Сочи'];
  const assets = ['USDT', 'USDT', 'BTC', 'ETH'] as const;
  const statuses = ['done', 'done', 'fixed', 'awaiting_payment', 'balance_check'] as const;
  const updatedLabels = ['Вчера 16:20', 'Вчера 12:05', '29.05.2024', '28.05.2024', '27.05.2024'];
  const managers = ['Иван П.', 'Мария К.', 'Олег Л.', 'Дмитрий В.', 'Сергей В.'];

  return Array.from({ length: count }, (_, i) => {
    const [clientName, clientShortName] = clients[i % clients.length];
    return {
      id: `#${13200 - 1 - i}`,
      clientName,
      clientShortName,
      dealType: i % 2 === 0 ? 'Продажа' : 'Покупка',
      asset: assets[i % assets.length],
      amountRub: ((i * 37) % 90 + 10) * 10_000,
      city: cities[i % cities.length],
      status: statuses[i % statuses.length],
      updatedLabel: updatedLabels[i % updatedLabels.length],
      createdBy: managers[i % managers.length],
      onKanban: false,
    };
  });
}

/** Мок-реализация сделок: компактный список для дашборда + данные страницы «Сделки» */
export class MockDealsService implements IDealsService {
  private async allDeals(): Promise<DealItem[]> {
    return [...REFERENCE_DEALS, ...generateArchiveDeals(50 - REFERENCE_DEALS.length)];
  }

  async getActiveDeals(): Promise<Deal[]> {
    return [
      {
        id: '#S-005',
        clientName: 'Gikk',
        operation: 'Продажа USDT',
        direction: 'USDT/RUB',
        amountRub: 7_800_000,
        assetAmount: '103 654.485 USDT',
        rate: 75.25,
        status: 'done',
        updatedMinutesAgo: 2,
      },
      {
        id: '#P-004',
        clientName: 'B Ekb',
        operation: 'Покупка USDT',
        direction: 'USDT/RUB',
        amountRub: 19_989_900,
        assetAmount: '266 000 USDT',
        rate: 75.15,
        status: 'fixed',
        updatedMinutesAgo: 5,
      },
      {
        id: '#S-003',
        clientName: 'Blato',
        operation: 'Продажа USDT',
        direction: 'USDT/RUB',
        amountRub: 2_390_505,
        assetAmount: '31 537 USDT',
        rate: 75.8,
        status: 'balance_check',
        insufficientUsdt: true,
        updatedMinutesAgo: 15,
      },
      {
        id: '#P-002',
        clientName: 'Поэты',
        operation: 'Покупка USDT',
        direction: 'USDT/RUB',
        amountRub: 2_370_375,
        assetAmount: '31 500 USDT',
        rate: 75.25,
        status: 'awaiting_payment',
        updatedMinutesAgo: 21,
      },
      {
        id: '#S-006',
        clientName: 'Кит Бокс',
        operation: 'Продажа USDT',
        direction: 'USDT/RUB',
        amountRub: 7_535_000,
        assetAmount: '100 000 USDT',
        rate: 75.35,
        status: 'done',
        updatedMinutesAgo: 34,
      },
    ];
  }

  async getDealsPage(): Promise<DealsPageData> {
    const deals = await this.allDeals();

    return {
      summaries: [
        { status: 'new', count: 1, totalRub: 173_771 },
        { status: 'fixed', count: 2, totalRub: 190_739 },
        { status: 'balance_check', count: 2, totalRub: 2_671_405 },
        { status: 'awaiting_payment', count: 2, totalRub: 2_454_541 },
        { status: 'in_delivery', count: 1, totalRub: 19_989_900 },
        { status: 'done', count: 2, totalRub: 15_335_000 },
        { status: 'canceled', count: 1, totalRub: 8_658 },
      ],
      cities: ['Екатеринбург', 'Челябинск', 'Москва', 'Тюмень', 'Другой город'],
      deals,
    };
  }

  async getDealById(id: string): Promise<DealDetails | null> {
    const decodedId = decodeURIComponent(id);
    const deal = (await this.allDeals()).find((item) => item.id === decodedId);
    if (!deal) return null;

    const qty = Math.max(1, Math.round(deal.amountRub / 75.5));
    const rate = Number((deal.amountRub / qty).toFixed(2));
    const isSale = deal.dealType === 'Продажа';

    return {
      deal,
      dealNo: decodedId.replace('#', ''),
      createdAt: deal.updatedLabel,
      updatedAt: deal.updatedLabel,
      source: deal.createdBy === 'Google Sheet' ? 'sheets' : 'crm',
      counterpartyName: deal.clientName,
      counterpartyPercent: deal.clientName === 'Gikk' ? 0 : 0.3,
      profitRub: Math.round(deal.amountRub * (isSale ? 0.012 : 0.007)),
      comment: deal.insufficientUsdt
        ? 'Сделка требует сверки баланса: недостаточно USDT с учетом активных заявок.'
        : 'Карточка собрана из текущего mock-среза Google Sheets.',
      tronscanUrl: deal.status === 'done' ? 'https://tronscan.org/#/transaction/mock' : undefined,
      legs: [
        {
          id: `${decodedId}-in`,
          direction: 'IN',
          currency: isSale ? 'USDT' : 'RUB',
          amount: isSale ? qty : deal.amountRub,
          rate,
          status: 'ACTIVE',
        },
        {
          id: `${decodedId}-out`,
          direction: 'OUT',
          currency: isSale ? 'RUB' : 'USDT',
          amount: isSale ? deal.amountRub : qty,
          rate,
          status: deal.status === 'canceled' ? 'CANCELED' : 'ACTIVE',
        },
      ],
      statusEvents: [
        {
          id: `${decodedId}-created`,
          oldStatus: null,
          newStatus: 'new',
          actorName: deal.createdBy,
          createdAt: deal.updatedLabel,
          comment: 'Создана заявка.',
        },
        ...(deal.status !== 'new'
          ? [
              {
                id: `${decodedId}-current`,
                oldStatus: 'new' as const,
                newStatus: deal.status,
                actorName: deal.createdBy,
                createdAt: deal.updatedLabel,
                comment: 'Текущий статус из mock-среза.',
              },
            ]
          : []),
      ],
    };
  }

  async getNewDealContext(): Promise<NewDealContext> {
    return {
      cities: ['Екатеринбург', 'Челябинск', 'Москва', 'Санкт-Петербург', 'Тюмень', 'Краснодар', 'Новосибирск', 'Уфа'],
      counterparties: [
        { id: 'ct-1', name: 'Алексей М' },
        { id: 'ct-2', name: 'Ренат' },
        { id: 'ct-3', name: '1exch|crypto' },
        { id: 'ct-4', name: 'Илхом' },
        { id: 'ct-5', name: 'Cassa Cassa' },
      ],
      clients: [
        { id: 'c-1', name: 'Gikk' },
        { id: 'c-2', name: 'Кит Бокс' },
        { id: 'c-3', name: 'Blato' },
        { id: 'c-4', name: 'BestChange' },
        { id: 'c-5', name: 'Андрей PE' },
        { id: 'c-6', name: 'Сергей С' },
      ],
      companyRates: {
        USDT: 81.377,
        BTC: 5_841_000,
        ETH: 316_000,
        CNY: 12.4,
      },
      defaultCounterpartyPercent: 0.5,
    };
  }

  async createClientTransfer(_payload: ClientTransferRequest): Promise<DealDetails> {
    void _payload;
    throw new Error('Переводы доступны только при подключённом CRM API');
  }

  async createExchange(_payload: ExchangeDealRequest): Promise<DealDetails> {
    void _payload;
    throw new Error('Создание обмена доступно только при подключённом CRM API');
  }

  async createCash(_payload: CashDealRequest): Promise<DealDetails> {
    void _payload;
    throw new Error('Создание кассовой заявки доступно только при подключённом CRM API');
  }

  async writeToTable(_id: string): Promise<DealDetails> {
    void _id;
    throw new Error('Запись в таблицу доступна только при подключённом CRM API');
  }

  async payReferrerSpread(_id: string, payload: import('@/types/referrerSpread').ReferrerSpreadPayRequest): Promise<DealDetails> {
    void _id;
    void payload;
    throw new Error('Перевод спреда КТ доступен только при подключённом CRM API');
  }

  async editSource(id: string, payload: DealSourceEditPayload): Promise<DealDetails> {
    void payload;
    const details = await this.getDealById(id);
    if (!details) throw new Error(`Deal ${id} was not found`);
    return details;
  }

  async cancel(id: string, comment?: string): Promise<DealDetails> {
    void comment;
    const details = await this.getDealById(id);
    if (!details) throw new Error(`Deal ${id} was not found`);
    details.deal.status = 'canceled';
    return details;
  }
}
