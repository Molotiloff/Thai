import Link from 'next/link';
import { Icon } from '@/components/ui/Icon';
import { CurrenciesPanel, type CashCurrencyCard } from '@/components/main/CurrenciesPanel';
import { FinancePanel } from '@/components/main/FinancePanel';
import { apiGet } from '../server';
import { labels, money, rows, type Row } from '../types';
import { Metrics, Page, Panel, PeriodFilter, Table, period, type Params, Notice } from '../ui';
import styles from '../thai.module.css';

export default async function Dashboard({ searchParams }: { searchParams: Promise<Params> }) {
  const p = period(await searchParams);
  const data = await apiGet<Row>('/reports/dashboard?period=' + p);
  const profit = data.profit as Row;
  const cash: CashCurrencyCard[] = rows(data.cash).map(row => ({
    instrumentCode: String(row.instrument_code), label: labels[String(row.instrument_code)] ?? String(row.instrument_code),
    amount: money(row.balance), carryingUsdt: money(row.carrying_usdt), unvaluedAmount: money(row.unvalued_quantity),
    hasUnvalued: !!row.unvalued_quantity && !/^0(?:\.0+)?$/.test(String(row.unvalued_quantity)),
    href: '/operations?account_id=' + encodeURIComponent(String(row.ledger_account_id)),
  }));
  return <Page title="Главная" subtitle="Финансовые показатели обменника. Общая касса, капитал и расчёты с партнёрами." actions={<Link href="/deals/new" className={styles.button}><Icon name="plus" size={15}/><span>Новая сделка</span></Link>}>
    <PeriodFilter value={p}/>
    <Metrics values={[
      ['Капитал в оборотке', data.capital_usdt, 'USDT'],
      ['Чистая прибыль месяца', profit.net_profit_usdt, 'USDT'],
      ['Проценты к выплате', data.interest_due_usdt, 'USDT'],
      ['Расходы месяца', profit.business_expenses_usdt, 'USDT'],
    ]}/>
    <div className={styles.dashboardMain}>
      <FinancePanel title="Результат месяца, USDT" compact indicators={[
        {id:'income',label:'Доход',value:money(profit.income_usdt)},
        {id:'expenses',label:'Расходы бизнеса',value:money(profit.business_expenses_usdt)},
        {id:'interest',label:'Проценты инвесторам',value:money(profit.investor_interest_usdt)},
        {id:'net',label:'Чистая прибыль',value:money(profit.net_profit_usdt),isNegative:String(profit.net_profit_usdt).startsWith('-')},
      ]} action={<Link className={styles.link} href={`/profit?period=${p}`}>Открыть отчёт<Icon name="chevron-right" size={13}/></Link>}/>
      <CurrenciesPanel currencies={cash}/>
    </div>
    <Notice>Остатки и задолженности показаны на текущий момент, прибыль за выбранный месяц. Наличный и безналичный бат учитываются отдельно. Себестоимость в USDT.</Notice>
    <div className={styles.split}>
      <Panel title="Клиентские обязательства" action={<Link className={styles.link} href="/loans">Займы<Icon name="chevron-right" size={13}/></Link>}>
        <Table data={rows(data.wallets)} columns={[{key:'instrument_code',title:'Счёт'},{key:'exchange_owes',title:'Обменник должен',amount:true},{key:'clients_owe',title:'Клиенты должны',amount:true},{key:'partner_loans',title:'Партнеры (сальдо)',amount:true}]}/>
      </Panel>
      <Panel title="Задолженности по расходам" action={<Link className={styles.link} href="/expenses">Выписка<Icon name="chevron-right" size={13}/></Link>}>
        <Table data={rows(data.expense_debts)} columns={[{key:'debtor',title:'Кто должен'},{key:'creditor',title:'Кому'},{key:'currency_code',title:'Валюта'},{key:'amount',title:'Сумма',amount:true}]}/>
      </Panel>
    </div>
    <Panel title="Источники прибыли месяца" action={<Link className={styles.link} href={`/profit?period=${p}`}>Все начисления<Icon name="chevron-right" size={13}/></Link>}>
      <Table data={rows(profit.sources)} columns={[{key:'operation_id',title:'Операция',href:'/operations/{operation_id}'},{key:'business_date',title:'Дата'},{key:'purpose',title:'Статья'},{key:'credit_signed_usdt',title:'Результат USDT',amount:true}]}/>
    </Panel>
    <div className={styles.quickLinks}>{[
      {href:'/turnover',label:'Капитал и инвесторы',icon:'pie' as const},
      {href:'/expenses',label:'Расчёты с партнёрами',icon:'expenses' as const},
      {href:'/best-change',label:'Сделки BestChange',icon:'deals' as const},
    ].map(item => <Link key={item.href} className={styles.quickLink} href={item.href}><Icon name={item.icon} size={18}/>{item.label}<Icon name="chevron-right" size={14}/></Link>)}</div>
  </Page>;
}
