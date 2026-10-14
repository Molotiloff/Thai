import {DealsView} from '@/components/deals/DealsView';
import {NewDealView} from '@/components/deals/new/NewDealView';
import {OPERATIONS,type Workspace} from '../deals/model';
import type {ClientOption} from '../deals/specs';
import {apiGet,session} from '../server';
import {OperationForms} from '../forms';
import {type FormSpec} from '../form-specs';
import {can,instruments,labels,rows,type Row} from '../types';
import {Page,Panel,Table,param,type Params} from '../ui';
export default async function Deals({searchParams}:{searchParams:Promise<Params>}){
 const q=await searchParams;const query=param(q,'query');const status=param(q,'status');const kind=param(q,'kind');const before=param(q,'before');const limit=['10','20','50','100'].includes(param(q,'limit'))?param(q,'limit'):'20';
 const filter=new URLSearchParams({query,limit});if(status)filter.set('status',status);if(kind)filter.set('kind',kind);if(before)filter.set('before',before);
 const [data,user]=await Promise.all([apiGet<Workspace>('/deals/workspace?'+filter),session()]);
 return <DealsView data={data} query={query} status={status} kind={kind} limit={limit} canCreate={can(user,'deal.post')||can(user,'finance.post')}/>;
}
export async function NewDeal({searchParams}:{searchParams:Promise<Params>}){
 const q=await searchParams;const value=param(q,'type');const type=OPERATIONS.find(o=>o.type===value)?.type??'sale';
 const clients:ClientOption[]=[];let after='';
 do {const data=await apiGet<Row>('/clients?limit=100'+(after?'&after_id='+after:''));clients.push(...rows(data.items).filter(c=>c.is_active).map(c=>[String(c.id),String(c.name)] as const));after=data.next_after_id?String(data.next_after_id):'';}while(after);
 return <NewDealView clients={clients} session={await session()} initialType={type}/>;
}
export async function DealDetail({params}:{params:Promise<{id:string}>}){
 const {id}=await params;const [data,user]=await Promise.all([apiGet<Row>('/deals/'+id),session()]);const attributes=data.attributes as Row;const incoming=attributes.receive as Row|undefined;const outgoing=attributes.pay as Row|undefined;
 const cashRequest=data.kind==='deposit'||data.kind==='withdrawal'||data.kind==='cash_exchange';
 const cashState=String(data.effective_status);
 if(cashRequest){
  const requestId='Б-'+id.padStart(6,'0');const fx=data.kind==='cash_exchange';
  const confirmations=rows(data.cash_confirmations);
  const legs:Row[]=fx?[{...incoming,direction:'receive'},{...outgoing,direction:'pay'}]:[{instrument:attributes.instrument,quantity:attributes.quantity,direction:data.kind==='deposit'?'receive':'pay'}];
  const actions:FormSpec[]=[];
  if(cashState==='draft'){
   const quantities=fx?legs.map(leg=>({name:leg.direction==='receive'?'receive_quantity':'pay_quantity',label:leg.direction==='receive'?'Принимаем: сумма':'Выдаём: сумма',kind:'money' as const,value:String(leg.quantity)})):[{name:'quantity',label:'Сумма',kind:'money' as const,value:String(attributes.quantity)}];
   actions.push({title:'Изменить заявку',path:`/cash-requests/${id}`,method:'PUT',permission:'deal.post',financial:false,fields:[...quantities,{name:'contact1',label:'Основной контакт',required:false,value:String(attributes.contact1??'')},{name:'contact2',label:'Второй контакт',required:false,value:String(attributes.contact2??'')},{name:'comment',label:'Комментарий',required:false,value:String(attributes.comment??'')}]});
   actions.push({title:'Готово к расчету',path:`/deals/${id}/ready`,permission:'deal.post',financial:false,fields:[]});
  }
  if(cashState==='ready')for(const leg of legs){
   if(fx&&confirmations.some(c=>c.direction===leg.direction))continue;
   actions.push({title:fx?(leg.direction==='receive'?'Подтвердить поступление':'Подтвердить выдачу'):'Провести фактический расчёт',path:`/cash-requests/${id}/settle`,permission:'finance.post',note:fx?'Касса изменится одной операцией после подтверждения обеих сторон.':'Сумма и валюта должны совпадать с заявкой. Дата расчёта — текущая (Бангкок). Для внесения и возврата батов курс не требуется.',fields:[{name:'instrument',label:'Счёт',value:String(leg.instrument),readOnly:true},{name:'signed_quantity',label:'Фактическая сумма со знаком',kind:'money',value:(leg.direction==='pay'?'-':'')+String(leg.quantity)},...((fx||!['THB_CASH','THB_BANK','USDT'].includes(String(leg.instrument)))?[{name:'quoted_rate',label:'Курс себестоимости: THB/USDT, остальные USDT/ед.',kind:'money' as const,required:false}]:[]),...(fx?[{name:'profit_revision_id',label:'ID ревизии прибыли',kind:'id' as const,required:false}]:[])]});
  }
  if(cashState==='draft'||cashState==='ready')actions.push({title:'Отменить заявку',path:`/deals/${id}/cancel`,permission:'deal.post',fields:[{name:'reason',label:'Причина'}]});
  const conditions=legs.map(leg=>({...leg,instrument:instruments.find(i=>i[0]===leg.instrument)?.[1]??leg.instrument,direction:leg.direction==='receive'?'Принимаем':'Выдаём',confirmed:confirmations.some(c=>c.direction===leg.direction)?'Подтверждено':cashState==='posted'?'Проведено':'Ожидается'}));
  return <Page back={{href:"/deals",label:"Сделки"}} title={`Заявка ${requestId}`} subtitle={`${fx?'Кассовый обмен':data.kind==='deposit'?'Внесение':'Выдача'} · ${labels[cashState]??cashState}`}><Panel title="Условия"><Table data={conditions} columns={[{key:'direction',title:'Направление'},{key:'instrument',title:'Счёт'},{key:'quantity',title:'Сумма',amount:true},...(fx?[{key:'confirmed',title:'Подтверждение'}]:[])]}/><Table data={[attributes]} columns={[{key:'client_name',title:'Клиент'},{key:'contact1',title:'Основной контакт'},{key:'contact2',title:'Второй контакт'},{key:'pin_code',title:'Код'},{key:'comment',title:'Комментарий'}]}/></Panel><Panel title="Заявка и расчёт"><OperationForms session={user} specs={actions}/></Panel><Panel title="Документы"><Table data={[data]} columns={[{key:'client_id',title:'Клиент',href:'/clients/{client_id}'},{key:'operation_id',title:'Фактическая операция',href:'/operations/{operation_id}'}]}/></Panel></Page>;
 }
 const state=String(data.effective_status);const specs:FormSpec[]=[{title:'Отметить готовой',path:`/deals/${id}/ready`,permission:'deal.post',financial:false,fields:[]},{title:'Провести сделку',path:`/deals/${id}/post`,permission:'deal.post',fields:[{name:'business_date',label:'Дата учёта',kind:'date',required:false},{name:'profit_revision_id',label:'ID ревизии прибыли',kind:'id',required:false}]},{title:'Отменить сделку',path:data.kind==='best_change'?`/profit/operations/${data.operation_id}/reversal`:`/deals/${id}/cancel`,permission:'finance.reverse',fields:[{name:'reason',label:'Причина'},{name:'business_date',label:'Дата учёта',kind:'date',required:false},{name:'profit_revision_id',label:'ID ревизии прибыли',kind:'id',required:false}]}].filter((_,index)=>index===0?state==='draft':index===1?state==='ready':state!=='cancelled') as FormSpec[];
 if(data.kind==='fx_trade'&&state==='posted')specs.unshift({
  title:'Исправить обмен',path:`/deals/${id}/correction`,permission:'finance.reverse',
  note:'Сторно и исправленная сделка проводятся вместе. При ошибке исходный обмен сохраняется.',
  fields:[
   {name:'expected_deal_id',label:'Текущая версия',kind:'id',value:id,readOnly:true},
   {name:'replacement.mode',label:'Способ учёта',value:String(attributes.mode),readOnly:true},
   {name:'replacement.receive.instrument',label:'Получаем: счёт',options:instruments,value:String(incoming?.instrument)},
   {name:'replacement.receive.quantity',label:'Получаем: сумма',kind:'money',value:String(incoming?.quantity)},
   {name:'replacement.receive.quoted_rate',label:'Получаем: курс себестоимости',kind:'money',required:false,value:String(incoming?.quoted_rate??'')},
   {name:'replacement.pay.instrument',label:'Выдаём: счёт',options:instruments,value:String(outgoing?.instrument)},
   {name:'replacement.pay.quantity',label:'Выдаём: сумма',kind:'money',value:String(outgoing?.quantity)},
   {name:'replacement.pay.quoted_rate',label:'Выдаём: курс себестоимости',kind:'money',required:false,value:String(outgoing?.quoted_rate??'')},
   {name:'replacement.comment',label:'Комментарий',required:false,value:String(attributes.comment??'')},
   {name:'reason',label:'Причина исправления'},
   {name:'business_date',label:'Дата учёта',kind:'date',required:false},
   {name:'profit_revision_id',label:'ID ревизии прибыли',kind:'id',required:false}
  ]
 });
 return <Page back={{href:"/deals",label:"Сделки"}} title={`Заявка #${data.request_id??id}`} subtitle={`Статус: ${labels[state]??state} · версия #${id}`}><Panel title="Условия"><Table data={[{...incoming,direction:'Получаем'},{...outgoing,direction:'Выдаём'}]} columns={[{key:'direction',title:'Направление'},{key:'instrument',title:'Счёт'},{key:'quantity',title:'Сумма',amount:true},{key:'quoted_rate',title:'Курс',amount:true}]}/></Panel><Panel title="Проведение и отмена"><OperationForms session={user} specs={specs}/></Panel><Panel title="Документы"><Table data={[data]} columns={[{key:'client_id',title:'Клиент',href:'/clients/{client_id}'},{key:'operation_id',title:'Операция',href:'/operations/{operation_id}'},{key:'replacement_deal_id',title:'Исправленная заявка',href:'/deals/{replacement_deal_id}'}]}/></Panel></Page>
}
