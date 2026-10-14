import {bestChangeForms,createTrade,type Field,type FormSpec} from '../form-specs';
import {instruments} from '../types';
import type {OperationType} from './model';
export type ClientOption=readonly [string,string];
const rate:Field={name:'quoted_rate',label:'Курс: THB за USDT; остальные USDT/ед.',kind:'money',required:false};
const day:Field={name:'business_date',label:'Дата учёта (Бангкок)',kind:'date',required:false};
const revision:Field={name:'profit_revision_id',label:'Ревизия прибыли (закрытый месяц)',kind:'id',required:false};
const quantity:Field={name:'quantity',label:'Сумма',kind:'money'};
const instrument:Field={name:'instrument',label:'Валюта / счёт',options:instruments};
const comment:Field={name:'comment',label:'Комментарий',required:false};
export function dealSpec(type:OperationType,clients:readonly ClientOption[]):FormSpec{
  const client=(name='client_id',label='Клиент',required=true):Field=>({name,label,kind:'id',options:clients,required,value:''});
  if(type==='best_change')return {...bestChangeForms[0],fields:bestChangeForms[0].fields.map(f=>f.name==='client_id'?client('client_id','Клиент',false):f)};
  if(type==='deposit'||type==='withdrawal')return {title:type==='deposit'?'Заявка на внесение':'Заявка на выдачу',path:'/cash-requests',permission:'deal.post',financial:false,fields:[client(),{name:'kind',label:'Тип заявки',value:type,readOnly:true},instrument,quantity,{name:'contact1',label:type==='deposit'?'Принимает':'Выдаёт',required:false},{name:'contact2',label:'Второй контакт',required:false},comment],note:'Создаётся заявка. Деньги учитываются после готовности и фактического кассового расчёта.'};
  if(type==='client_transfer')return {title:'Перевод клиент → клиент',path:'/clients/transfers',permission:'finance.post',fields:[client('source_client_id','Отправитель'),client('target_client_id','Получатель'),instrument,quantity,rate,day,comment,revision],note:'Перевод между кошельками в одной валюте. Общая касса не меняется.'};
  if(type==='thb_transfer')return {title:'Наличный ↔ безналичный бат',path:'/wallet-transfers',permission:'finance.post',fields:[client(),{name:'source_instrument',label:'Со счёта',options:instruments.slice(0,2)},{name:'target_instrument',label:'На счёт',options:instruments.slice(0,2),value:'THB_BANK'},quantity,{name:'fee',label:'Комиссия THB',kind:'money',value:'0'},{...rate,required:true},{name:'physical',label:'Также переместить деньги в кассе',kind:'checkbox'},day,revision]};
  const fields=createTrade.fields.map(f=>{
    if(f.name==='client_id')return client();
    if(f.name==='mode')return {...f,label:'Тип обмена',value:'wallet',options:[['wallet','Обмен на балансах клиента'],['cash','Фактический обмен в кассе']] as const};
    if(f.name==='receive.quoted_rate')return {name:'exchange.rate',label:'Прямой курс: выдаём за 1 получаем',kind:'money' as const,required:false,transient:true};
    if(f.name==='pay.quoted_rate')return {name:'exchange.inverse_rate',label:'Обратный курс: получаем за 1 выдаём',kind:'money' as const,required:false,transient:true};
    if(type==='sale'&&f.name==='receive.instrument')return {...f,options:instruments.slice(0,2)};
    if(type==='sale'&&f.name==='pay.instrument')return {...f,options:instruments.slice(2)};
    if(type==='purchase'&&f.name==='receive.instrument')return {...f,options:instruments.slice(2),value:'USDT'};
    if(type==='purchase'&&f.name==='pay.instrument')return {...f,options:instruments.slice(0,2),value:'THB_CASH'};
    return f;
  });
  return {...createTrade,fields:[...fields,comment],note:'Обмен на балансах проводится как команда бота: меняются кошельки клиента, карточки отправляются в Telegram. Фактический обмен в кассе создаёт заявку и проводится после подтверждения обеих сторон. Номер присваивается автоматически.'};
}
