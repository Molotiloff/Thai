import {apiGet,optionalGet,session} from '../server';
import {OperationForms} from '../forms';
import {bestChangeForms} from '../form-specs';
import {rows,type Row} from '../types';
import {Page,Panel,Table,Metrics,PeriodFilter,More,param,period,type Params,opColumn,Notice} from '../ui';
export default async function BestChange({searchParams}:{searchParams:Promise<Params>}){
 const q=await searchParams;const p=period(q);const before=param(q,'before_id');
 const [data,bindings,obligations,user]=await Promise.all([apiGet<Row>('/best-change/deals?limit=50'+(before?'&before_id='+encodeURIComponent(before):'')),apiGet<Row|null>('/best-change/bindings'),apiGet<Row>('/profit/obligations'),session()]);
 const report=bindings?await optionalGet<Row>('/best-change/report?period='+p):null;
 return <Page title="BestChange" subtitle="Сделки в THB, комиссия платформы и доли результата в USDT."><PeriodFilter value={p}/>{report?<Metrics values={[
 ['Сделок за месяц',report.count],['Комиссия',report.fee_usdt,'USDT'],['Доля Данияра',report.daniyar_usdt,'USDT'],['Доля бизнеса',report.business_usdt,'USDT'],['Буфер Данияра (сейчас)',(report.buffer as Row)?.balance,'USDT'],
 ]}/>:<Notice>Служебные счета пока не подключены. Обратитесь к администратору.</Notice>}<Notice>Доля Данияра сначала хранится в буфере чата BestChange и переносится на «Баланс Данияр» при закрытии прибыли.</Notice><Panel title="Сделки"><OperationForms session={user} specs={bestChangeForms}/></Panel><Panel title="История сделок"><Table data={rows(data.items)} columns={[{key:'deal_id',title:'Сделка'},opColumn,{key:'business_date',title:'Дата'},{key:'quantity_usdt',title:'Количество USDT',amount:true},{key:'market_rate',title:'Рынок THB/USDT',amount:true},{key:'client_rate',title:'Клиент THB/USDT',amount:true},{key:'platform_fee_usdt',title:'Комиссия USDT',amount:true},{key:'daniyar_share_usdt',title:'Данияр USDT',amount:true},{key:'business_share_usdt',title:'Бизнес USDT',amount:true},{key:'active',title:'Действует'}]}/><More href={`/best-change?period=${p}`} cursor={data.next_before_id} name="before_id"/></Panel><Panel title="Комиссия к выплате"><Table data={rows(obligations.platform)} columns={[opColumn,{key:'business_date',title:'Дата'},{key:'platform_fee_usdt',title:'Начислено USDT',amount:true},{key:'remaining_usdt',title:'К выплате USDT',amount:true}]}/></Panel><Panel title="Переносы Данияру"><Table data={rows(report?.transfers)} columns={[opColumn,{key:'period',title:'Месяц'},{key:'amount_usdt',title:'USDT',amount:true},{key:'revision',title:'Ревизия'}]}/></Panel></Page>
}
