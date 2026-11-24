import {activeClientOptions,apiGet,optionalGet,session} from '../server';
import {OperationForms} from '../forms';
import {bestChangeForms} from '../form-specs';
import {bahtTradeType,operationLabel} from '../deals/model';
import {can,rows,type Row} from '../types';
import {Page,Panel,Table,Metrics,PeriodFilter,More,param,period,type Params,opColumn,Notice} from '../ui';
export default async function BestChange({searchParams}:{searchParams:Promise<Params>}){
 const q=await searchParams;const p=period(q);const before=param(q,'before_id');
 const [data,bindings,obligations,user]=await Promise.all([apiGet<Row>('/best-change/deals?limit=50'+(before?'&before_id='+encodeURIComponent(before):'')),apiGet<Row|null>('/best-change/bindings'),apiGet<Row>('/profit/obligations'),session()]);
 const [report,clients]=await Promise.all([
  bindings?optionalGet<Row>('/best-change/report?period='+p):null,
  can(user,'deal.post')?activeClientOptions():[],
 ]);
 const specs=bestChangeForms.map(spec=>({...spec,fields:spec.fields.map(field=>
  field.name==='client_id'?{...field,options:clients,searchable:true}:field,
 )}));
 const deals=rows(data.items).map(row=>{
  const attributes=row.attributes as Row;const receive=attributes.receive as Row;const pay=attributes.pay as Row;
  return {...row,direction:operationLabel(bahtTradeType(receive.instrument,pay.instrument))+' батов'};
 });
 return <Page title="BestChange" subtitle="Сделки в THB, комиссия платформы и доли результата в USDT."><PeriodFilter value={p}/>{report?<Metrics values={[
 ['Сделок за месяц',report.count],['Комиссия',report.fee_usdt,'USDT'],['Доля Данияра',report.daniyar_usdt,'USDT'],['Доля бизнеса',report.business_usdt,'USDT'],['Баланс Данияра (сейчас)',(report.daniyar_balance as Row)?.balance,'USDT'],
 ]}/>:<Notice>Служебные счета пока не подключены. Обратитесь к администратору.</Notice>}<Notice>Доля Данияра сразу зачисляется на «Баланс Данияр» с сообщением «Прибыль с Бест». Комиссия CoinDrop накапливается за месяц и выплачивается отдельно после его окончания.</Notice><Panel title="Сделки"><OperationForms session={user} specs={specs}/></Panel><Panel title="История сделок"><Table data={deals} columns={[{key:'direction',title:'Направление'},{key:'deal_id',title:'Сделка'},opColumn,{key:'business_date',title:'Дата'},{key:'quantity_usdt',title:'Количество USDT',amount:true},{key:'market_rate',title:'Рынок THB/USDT',amount:true},{key:'client_rate',title:'Клиент THB/USDT',amount:true},{key:'platform_fee_usdt',title:'Комиссия USDT',amount:true},{key:'daniyar_share_usdt',title:'Данияр USDT',amount:true},{key:'business_share_usdt',title:'Бизнес USDT',amount:true},{key:'active',title:'Действует'}]}/><More href={`/best-change?period=${p}`} cursor={data.next_before_id} name="before_id"/></Panel><Panel title="CoinDrop: накоплено к расчёту"><Table data={rows(obligations.platform)} columns={[opColumn,{key:'business_date',title:'Дата'},{key:'platform_fee_usdt',title:'Начислено USDT',amount:true},{key:'remaining_usdt',title:'К выплате USDT',amount:true}]}/></Panel><Panel title="Исторические переносы Данияру"><Table data={rows(report?.transfers)} columns={[opColumn,{key:'period',title:'Месяц'},{key:'amount_usdt',title:'USDT',amount:true}]}/></Panel></Page>
}
