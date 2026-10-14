import Link from 'next/link';
import { apiGet } from '../server';
import { rows,type Row } from '../types';
import { Metrics,Page,Panel,PeriodFilter,Table,period,type Params,Notice } from '../ui';
export default async function Dashboard({searchParams}:{searchParams:Promise<Params>}) {
 const p=period(await searchParams);const data=await apiGet<Row>('/reports/dashboard?period='+p);const profit=data.profit as Row;
 return <Page title="Главная" subtitle="Одна общая касса. Наличный и безналичный бат — отдельные счета."><PeriodFilter value={p}/><Metrics values={[
 ['Капитал в оборотке',data.capital_usdt,'USDT'],['Чистая прибыль месяца',profit.net_profit_usdt,'USDT'],['Проценты к выплате',data.interest_due_usdt,'USDT'],
 ]}/><Link href="/turnover">Капитал и начисления инвесторам →</Link><Notice>Остатки и задолженности — на текущий момент; прибыль — за выбранный месяц. Себестоимость хранится в USDT. Займы уже входят в обязательства перед клиентами.</Notice>
 <Panel title="Деньги в кассе"><Table data={rows(data.cash)} columns={[{key:'instrument_code',title:'Валюта'},{key:'balance',title:'Остаток',amount:true},{key:'carrying_usdt',title:'Учтённая себестоимость USDT',amount:true},{key:'unvalued_quantity',title:'Без себестоимости (в валюте)',amount:true},{key:'ledger_account_id',title:'Движения',href:'/operations?account_id={ledger_account_id}'}]}/></Panel>
 <Panel title="Клиентские обязательства"><Table data={rows(data.wallets)} columns={[{key:'instrument_code',title:'Счёт'},{key:'exchange_owes',title:'Обменник должен',amount:true},{key:'clients_owe',title:'Клиенты должны',amount:true},{key:'partner_loans',title:'В том числе Партнеры (сальдо)',amount:true}]}/><Link href="/loans">Открыть займы →</Link></Panel>
 <Panel title="Задолженности по расходам"><Table data={rows(data.expense_debts)} columns={[{key:'debtor',title:'Кто должен'},{key:'creditor',title:'Кому'},{key:'currency_code',title:'Валюта'},{key:'amount',title:'Сумма',amount:true}]}/><Link href="/expenses">Расчёты и выписка →</Link></Panel>
 <Panel title="Источники прибыли месяца"><Table data={rows(profit.sources)} columns={[{key:'operation_id',title:'Операция',href:'/operations/{operation_id}'},{key:'business_date',title:'Дата'},{key:'purpose',title:'Статья'},{key:'credit_signed_usdt',title:'Результат USDT',amount:true}]}/><Link href={`/profit?period=${p}`}>Прибыль и выплаты →</Link></Panel></Page>
}
