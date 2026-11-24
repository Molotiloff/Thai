'use client';
import {useRef,useState,useSyncExternalStore} from 'react';
import {useRouter} from 'next/navigation';
import {NewDealHeader} from './NewDealHeader';
import {DealTypeTabs} from './DealTypeTabs';
import {DealDataCard} from './DealDataCard';
import {AutoCalcCard,type CalcRow} from './AutoCalcCard';
import layout from './NewDealView.module.css';
import styles from '@/thai/deals/deals.module.css';
import {ActionForm} from '@/thai/forms';
import {can,money,type Session} from '@/thai/types';
import {OPERATIONS,calculateCounterparty,calculateExchangeForm,instrumentLabel,type ExchangeAnchor,type ExchangeRateSource,type OperationType} from '@/thai/deals/model';
import {dealSpec,type ClientOption} from '@/thai/deals/specs';
const subscribe=()=>()=>{};
const ready=()=>true;
const notReady=()=>false;
const initial:Record<string,string>={};
export function NewDealView({clients,counterparties=[],session,initialType='sale',today}:{clients:ClientOption[];counterparties?:ClientOption[];session:Session;initialType?:OperationType;today?:string}){
  const router=useRouter();const hydrated=useSyncExternalStore(subscribe,ready,notReady);
  const [type,setType]=useState<OperationType>(initialType);const [values,setValues]=useState(initial);const [busy,setBusy]=useState(false);const [submitted,setSubmitted]=useState(false);
  const spec=dealSpec(type,clients,today,counterparties);const permitted=can(session,spec.permission);
  const anchor=useRef<ExchangeAnchor>('receive');
  const rateSource=useRef<ExchangeRateSource>('amounts');
  const isExchange=['sale','purchase','conversion'].includes(type);
  const selectedInstrument=(name:string)=>values[name]||spec.fields.find(field=>field.name===name)?.value||spec.fields.find(field=>field.name===name)?.options?.[0]?.[0]||'';
  if(isExchange)spec.fields=spec.fields.map(field=>field.name==='exchange.rate'?{...field,label:`Прямой курс: ${instrumentLabel(selectedInstrument('pay.instrument'))} за 1 ${instrumentLabel(selectedInstrument('receive.instrument'))}`}:
    field.name==='exchange.inverse_rate'?{...field,label:`Обратный курс: ${instrumentLabel(selectedInstrument('receive.instrument'))} за 1 ${instrumentLabel(selectedInstrument('pay.instrument'))}`}:field);
  function deriveValues(current:Record<string,string>,changed:string,phase:'input'|'blur'){
    const result=calculateExchangeForm(current,changed,anchor.current,phase,rateSource.current);
    anchor.current=result.anchor;rateSource.current=result.rateSource;return result.updates;
  }
  function change(next:OperationType){anchor.current='receive';rateSource.current='amounts';setType(next);setValues(initial);setSubmitted(false);}
  const kt=calculateCounterparty({...Object.fromEntries(spec.fields.map(f=>[f.name,f.value??f.options?.[0]?.[0]??''])),...values});
  const calcRows:CalcRow[]=type==='profit'?[
    {label:'Прибыль / убыток USDT',text:money(values.amount_usdt)},
    {label:'Дата учёта',text:values.business_date||today||'Сегодня'},
    {label:'Количество батов',text:values.thb_amount?money(values.thb_amount):'Не указано'},
  ]:[
    {label:'Получаем / переводим',text:money(values['receive.quantity']??values.quantity)},
    {label:'Выдаём',text:money(values['pay.quantity'])},
    ...(isExchange?[{label:'Прямой курс',text:values['exchange.rate']||'—'},{label:'Обратный курс',text:values['exchange.inverse_rate']||'—'},{label:'Номер заявки',text:'Присваивается автоматически'}]:[{label:'Комиссия перевода THB',text:money(values.fee??'0')}]),
    {label:'Учёт в USDT',text:'Курс определяется по обменам'},
    ...(kt?[{label:'Начисление КТ USDT',text:kt.amount},{label:'Курс обменника THB за 1 USDT',text:kt.rate}]:[])
  ];
  return <><NewDealHeader formId="new-deal" disabled={!hydrated||busy||submitted||!permitted}/><DealTypeTabs active={type} onChange={change} disabled={!hydrated||busy}/><div className={layout.grid}>
    <DealDataCard title={OPERATIONS.find(o=>o.type===type)?.label??type} note={spec.note}>
      {permitted?<ActionForm key={type} spec={spec} formId="new-deal" className={styles.form} submitLabel="Добавить сделку" onValuesChange={setValues} deriveValues={isExchange?deriveValues:undefined} onBusyChange={setBusy} onSuccess={result=>{setSubmitted(true);router.push(result.deal_id?'/deals/'+result.deal_id:'/operations/'+result.id);}}/>:<p>Ваша роль позволяет просматривать сделки. Для проведения требуется право {spec.permission}.</p>}
    </DealDataCard>
    <AutoCalcCard title={type==='profit'?'Результат сделки':undefined} rows={calcRows} note={type==='profit'?'Результат войдёт в отчёт прибыли за выбранный месяц. Никаких дополнительных комиссий или движений денег.':isExchange?undefined:'Кошелёк клиента может уходить в минус. Номер документа присваивается автоматически.'}/>
  </div></>;
}
