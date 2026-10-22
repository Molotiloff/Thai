import {apiGet,optionalGet,session} from '../server';
import {OperationForms} from '../forms';
import {profitActions} from '../form-specs';
import {named,rows,type Row} from '../types';
import {Page,Panel,Table,Metrics,PeriodFilter,period,type Params,Notice,opColumn} from '../ui';
export default async function Profit({searchParams}:{searchParams:Promise<Params>}){
 const q=await searchParams;const p=period(q);
 const [data,obligations,terms,closure,revisions,user,parties]=await Promise.all([apiGet<Row>('/profit/report?period='+p),apiGet<Row>('/profit/obligations'),apiGet<Row[]>('/profit/terms'),optionalGet<Row>(`/profit/closures?period=${p}`),apiGet<Row[]>('/profit/revisions?period='+p),session(),apiGet<Row[]>('/expense-participants')]);
 return <Page title="Прибыль" subtitle="USDT · доходы минус расходы бизнеса и утверждённые проценты инвесторам."><PeriodFilter value={p}/><Metrics values={[
 ['Доход',data.income_usdt,'USDT'],['Общие расходы',data.business_expenses_usdt,'USDT'],['Проценты инвесторам',data.investor_interest_usdt,'USDT'],['Чистая прибыль',data.net_profit_usdt,'USDT'],
 ]}/><Notice>Данияр получает только долю BestChange. Капитал, займы и погашение начислений не входят в доход. Убыток остаётся в отчёте своего месяца.</Notice>{data.needs_revision?<Notice>Текущий расчёт отличается от зафиксированного закрытия. Для корректировки создайте отдельную зеркальную заявку в открытом периоде.</Notice>:null}
 <Panel title="Закрытие, выплаты и условия"><OperationForms session={user} specs={profitActions}/></Panel>
 <Panel title="Начисления собственникам к выплате"><Table data={named(rows(obligations.owners),parties)} columns={[opColumn,{key:'period',title:'Месяц'},{key:'owner_name',title:'Собственник'},{key:'amount_usdt',title:'Начислено USDT',amount:true},{key:'remaining_usdt',title:'Остаток USDT',amount:true}]}/></Panel><Panel title="Начисления выбранного закрытия"><Table data={named(rows(closure?.accruals),parties)} columns={[{key:'owner_name',title:'Собственник'},{key:'amount_usdt',title:'Начислено USDT',amount:true}]} empty="Месяц ещё не закрыт или начислений нет"/>{closure?<Table data={[closure]} columns={[opColumn,{key:'net_profit_usdt',title:'Зафиксированная прибыль USDT',amount:true}]}/>:null}</Panel>
 <Panel title="Источники результата"><Table data={rows(closure?.sources??data.sources)} columns={[{key:closure?'source_operation_id':'operation_id',title:'Операция',href:closure?'/operations/{source_operation_id}':'/operations/{operation_id}'},{key:'account_id',title:'Счёт',href:'/operations?account_id={account_id}'},{key:'purpose',title:'Статья'},{key:'credit_signed_usdt',title:'Результат USDT',amount:true}]}/></Panel>
 <Panel title="История договорённостей"><Table data={terms} columns={[{key:'valid_from',title:'Действует с'},{key:'skyex_percent',title:'SkyEX %',amount:true},{key:'pe_percent',title:'ПЕ %',amount:true},opColumn]}/></Panel><Panel title="История исправлений прибыли"><Table data={revisions} columns={[opColumn,{key:'reason',title:'Причина'},{key:'completed_by_operation_id',title:'Закрытие',href:'/operations/{completed_by_operation_id}'}]}/></Panel></Page>
}
