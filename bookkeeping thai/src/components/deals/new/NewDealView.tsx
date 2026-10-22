'use client';
import {useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {useRouter} from 'next/navigation';
import {NewDealHeader} from './NewDealHeader';
import {DealTypeTabs} from './DealTypeTabs';
import {DealDataCard} from './DealDataCard';
import {AutoCalcCard,type CalcRow} from './AutoCalcCard';
import layout from './NewDealView.module.css';
import styles from '@/thai/deals/deals.module.css';
import {ActionForm} from '@/thai/forms';
import {can,money,type Row,type Session} from '@/thai/types';
import {OPERATIONS,calculateExchangeForm,instrumentLabel,type ExchangeAnchor,type OperationType} from '@/thai/deals/model';
import {dealSpec,type ClientOption} from '@/thai/deals/specs';
const subscribe=()=>()=>{};
const ready=()=>true;
const notReady=()=>false;
const initial:Record<string,string>={};
export function NewDealView({clients,session,initialType='sale'}:{clients:ClientOption[];session:Session;initialType?:OperationType}){
  const router=useRouter();const hydrated=useSyncExternalStore(subscribe,ready,notReady);
  const [type,setType]=useState<OperationType>(initialType);const [values,setValues]=useState(initial);const [busy,setBusy]=useState(false);const [submitted,setSubmitted]=useState(false);
  const [preview,setPreview]=useState<Row|null>(null);
  const spec=dealSpec(type,clients);const permitted=can(session,spec.permission);
  const anchor=useRef<ExchangeAnchor>('receive');
  const isExchange=['sale','purchase','conversion'].includes(type);
  const selectedInstrument=(name:string)=>values[name]||spec.fields.find(field=>field.name===name)?.value||spec.fields.find(field=>field.name===name)?.options?.[0]?.[0]||'';
  if(isExchange)spec.fields=spec.fields.map(field=>field.name==='exchange.rate'?{...field,label:`Прямой курс: ${instrumentLabel(selectedInstrument('pay.instrument'))} за 1 ${instrumentLabel(selectedInstrument('receive.instrument'))}`}:
    field.name==='exchange.inverse_rate'?{...field,label:`Обратный курс: ${instrumentLabel(selectedInstrument('receive.instrument'))} за 1 ${instrumentLabel(selectedInstrument('pay.instrument'))}`}:field);
  function deriveValues(current:Record<string,string>,changed:string){
    const result=calculateExchangeForm(current,changed,anchor.current);anchor.current=result.anchor;return result.updates;
  }
  useEffect(()=>{
    if(type!=='best_change')return;
    const controller=new AbortController();
    const timer=setTimeout(async()=>{
      try{
        const response=await fetch('/api/thai/best-change/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:values.operation??'sale',quantity:values.quantity??'',market_rate:values.market_rate??'',client_rate:values.client_rate??''}),signal:controller.signal});
        const data=await response.json();if(!controller.signal.aborted)setPreview(response.ok?data:null);
      }catch{if(!controller.signal.aborted)setPreview(null);}
    },250);
    return ()=>{clearTimeout(timer);controller.abort();};
  },[type,values]);
  function change(next:OperationType){anchor.current='receive';setType(next);setValues(initial);setPreview(null);setSubmitted(false);}
  const calcRows:CalcRow[]=type==='best_change'?[
    {label:'Количество USDT',text:money(values.quantity)},
    {label:'Сумма THB',text:money(preview?.thb_quantity)},
    {label:'Спред USDT',text:money(preview?.gross_usdt)},
    {label:'Комиссия BestChange USDT',text:money(preview?.fee_usdt)},
    {label:'Данияр USDT',text:money(preview?.daniyar_usdt)},
    {label:'Прибыль бизнеса USDT',text:money(preview?.business_usdt)}
  ]:[
    {label:'Получаем / переводим',text:money(values['receive.quantity']??values.quantity)},
    {label:'Выдаём',text:money(values['pay.quantity'])},
    ...(isExchange?[{label:'Прямой курс',text:values['exchange.rate']||'—'},{label:'Обратный курс',text:values['exchange.inverse_rate']||'—'},{label:'Номер заявки',text:'Присваивается автоматически'}]:[{label:'Комиссия перевода THB',text:money(values.fee??'0')}]),
    {label:'Учёт в USDT',text:'Курс определяется по обменам'}
  ];
  return <><NewDealHeader formId="new-deal" disabled={!hydrated||busy||submitted||!permitted}/><DealTypeTabs active={type} onChange={change} disabled={!hydrated||busy}/><div className={layout.grid}>
    <DealDataCard title={OPERATIONS.find(o=>o.type===type)?.label??type} note={spec.note}>
      {permitted?<ActionForm key={type} spec={spec} formId="new-deal" className={styles.form} submitLabel="Добавить сделку" onValuesChange={setValues} deriveValues={isExchange?deriveValues:undefined} onBusyChange={setBusy} onSuccess={result=>{setSubmitted(true);router.push(result.deal_id?'/deals/'+result.deal_id:'/operations/'+result.id);}}/>:<p>Ваша роль позволяет просматривать сделки. Для проведения требуется право {spec.permission}.</p>}
    </DealDataCard>
    <AutoCalcCard rows={calcRows} note={type==='best_change'?'Расчёт выполняет backend по тем же правилам, что применяются при проведении.':isExchange?'Введите одну сумму и прямой либо обратный курс пары — вторая сумма подставится автоматически. Оценка в USDT определяется автоматически, по тем же правилам, что и в боте.':'Кошелёк клиента может уходить в минус. Номер документа присваивается автоматически.'}/>
  </div></>;
}
