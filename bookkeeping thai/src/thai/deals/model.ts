import {instruments,money,type Row} from '../types';
import type {IconName} from '@/components/ui/Icon';
export const OPERATIONS = [
  {type:'sale',label:'Продажа',icon:'deals'},
  {type:'purchase',label:'Покупка',icon:'wallet'},
  {type:'deposit',label:'Внесение',icon:'income'},
  {type:'withdrawal',label:'Выдача',icon:'expenses'},
  {type:'client_transfer',label:'Перевод',icon:'repeat'},
  {type:'conversion',label:'Конвертация',icon:'repeat'},
  {type:'thb_transfer',label:'Нал / безнал',icon:'shuffle'},
  {type:'best_change',label:'BestChange',icon:'coins'},
  {type:'profit',label:'Прибыль',icon:'income'},
] as const satisfies readonly {type:string;label:string;icon:IconName}[];
export type OperationType=typeof OPERATIONS[number]['type'];
export const STATUSES = [
  {code:'draft',label:'Новая',tone:'fixed',icon:'plus'},
  {code:'ready',label:'Готова к проведению',tone:'check',icon:'scale'},
  {code:'posted',label:'Сделка завершена',tone:'done',icon:'check-circle'},
  {code:'cancelled',label:'Отменена',tone:'alert',icon:'x-circle'},
] as const satisfies readonly {code:string;label:string;tone:string;icon:IconName}[];
export interface DealEntry {
  entry_id:string;deal_id:number|null;operation_id:number|null;client_id:number|null;
  client_name:string;created_by:string;created_at:string;status:string;kind:OperationType;
  attributes:Row;href:string;
}
export interface Workspace {
  items:DealEntry[];board:DealEntry[];summaries:{status:string;count:number}[];next_cursor:string|null;
}
export function operationLabel(kind:string):string {return OPERATIONS.find(o=>o.type===kind)?.label??kind;}
export function bahtTradeType(receive:unknown,pay:unknown):'sale'|'purchase'|'conversion' {
  const receivesThb=['THB_CASH','THB_BANK'].includes(String(receive));
  const paysThb=['THB_CASH','THB_BANK'].includes(String(pay));
  return paysThb&&!receivesThb?'sale':receivesThb&&!paysThb?'purchase':'conversion';
}
export function instrumentLabel(code:unknown):string {return instruments.find(i=>i[0]===code)?.[1]??String(code??'');}
export function entryDirection(entry:DealEntry):string {
  const a=entry.attributes;
  if(entry.kind==='profit')return 'Итоговый результат · USDT';
  if(a.receive&&a.pay) return `${instrumentLabel((a.receive as Row).instrument)} → ${instrumentLabel((a.pay as Row).instrument)}`;
  if(entry.kind==='client_transfer') return `${entry.client_name} → ${String(a.target_name??'Клиент #'+a.target_client)}`;
  if(entry.kind==='thb_transfer') return `${instrumentLabel(a.source)} → ${instrumentLabel(a.target)}`;
  return instrumentLabel(a.instrument);
}
export function entryAmount(entry:DealEntry):string {
  const a=entry.attributes;
  if(entry.kind==='profit')return `${money(a.amount_usdt)} USDT${a.thb_amount?` · ${money(a.thb_amount)} THB`:''}`;
  if(a.receive&&a.pay) return `${money((a.receive as Row).quantity)} ${instrumentLabel((a.receive as Row).instrument)} / ${money((a.pay as Row).quantity)} ${instrumentLabel((a.pay as Row).instrument)}`;
  return `${money(a.quantity)} ${instrumentLabel(a.instrument??a.source)}`;
}
export function timestamp(value:string):string {return new Intl.DateTimeFormat('ru-RU',{timeZone:'Asia/Bangkok',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(value));}


type DecimalRatio={numerator:bigint;denominator:bigint};
export type ExchangeAnchor='receive'|'pay';
export type ExchangeRateSource='manual'|'amounts';
function positiveDecimal(value:string|undefined):DecimalRatio|null {
  const text=(value??'').trim().replace(',','.');
  if(text.length>80||!/^\d+(?:\.\d*)?$/.test(text))return null;
  const [whole,fraction='']=text.split('.');
  const numerator=BigInt(whole+fraction);
  return numerator>0n?{numerator,denominator:10n**BigInt(fraction.length)}:null;
}
function ratioText(numerator:bigint,denominator:bigint,precision:number,trim=false):string {
  const scale=10n**BigInt(precision);
  const scaled=(numerator*scale*2n+denominator)/(denominator*2n);
  const text=`${scaled/scale}.${String(scaled%scale).padStart(precision,'0')}`;
  return trim?text.replace(/\.?0+$/,''):text;
}
export function calculateExchangeForm(values:Record<string,string>,changed:string,anchor:ExchangeAnchor='receive',phase:'input'|'blur'='input',rateSource:ExchangeRateSource='manual') {
  const updates:Record<string,string>={};
  const result=()=>({anchor,updates,rateSource});
  if(changed==='receive.quantity')anchor='receive';
  if(changed==='pay.quantity')anchor='pay';
  if(changed==='receive.instrument'||changed==='pay.instrument') {
    return {anchor,rateSource:'amounts' as const,updates:{'exchange.rate':'','exchange.inverse_rate':'',[`${anchor==='receive'?'pay':'receive'}.quantity`]:''}};
  }
  if(!['receive.quantity','pay.quantity','exchange.rate','exchange.inverse_rate'].includes(changed))return result();
  const receive=positiveDecimal(values['receive.quantity']);
  const pay=positiveDecimal(values['pay.quantity']);
  let rate=positiveDecimal(values['exchange.rate']);
  if(changed.endsWith('.quantity')&&(rateSource==='amounts'||!rate)) {
    rateSource='amounts';
    // Amounts are authoritative in this mode. Never feed the inferred rate
    // back into the opposite amount, including when an amount is edited again.
    if(phase==='blur'&&receive&&pay) {
      updates['exchange.rate']=ratioText(pay.numerator*receive.denominator,pay.denominator*receive.numerator,18,true);
      updates['exchange.inverse_rate']=ratioText(receive.numerator*pay.denominator,receive.denominator*pay.numerator,18,true);
    }else{
      updates['exchange.rate']='';updates['exchange.inverse_rate']='';
    }
    return result();
  }
  if(changed.startsWith('exchange.')) {
    if(phase==='blur')return result();
    rateSource='manual';
  }
  if(changed==='exchange.inverse_rate') {
    const inverse=positiveDecimal(values['exchange.inverse_rate']);
    rate=inverse?{numerator:inverse.denominator,denominator:inverse.numerator}:null;
  }
  if(changed.startsWith('exchange.')&&!rate) {
    updates[changed==='exchange.rate'?'exchange.inverse_rate':'exchange.rate']='';
    updates[`${anchor==='receive'?'pay':'receive'}.quantity`]='';
    return result();
  }
  if(!rate)return result();
  if(changed.endsWith('.quantity')&&!(anchor==='receive'?receive:pay)) {
    updates[`${anchor==='receive'?'pay':'receive'}.quantity`]='';
    return result();
  }
  if(changed!=='exchange.rate')updates['exchange.rate']=ratioText(rate.numerator,rate.denominator,18,true);
  if(changed!=='exchange.inverse_rate')updates['exchange.inverse_rate']=ratioText(rate.denominator,rate.numerator,18,true);
  if((anchor==='receive'&&receive)||!pay) {
    if(receive)updates['pay.quantity']=ratioText(receive.numerator*rate.numerator,
      receive.denominator*rate.denominator,values['pay.instrument']==='USDT'?6:2);
  }else if(pay)updates['receive.quantity']=ratioText(pay.numerator*rate.denominator,
    pay.denominator*rate.numerator,values['receive.instrument']==='USDT'?6:2);
  return result();
}

// Integer ratios preserve the same single HALF_UP rounding as the backend.
export function calculateCounterparty(values:Record<string,string>):{amount:string;rate:string}|null {
  if(!values.counterparty_client_id)return null;
  const receivesThb=['THB_CASH','THB_BANK'].includes(values['receive.instrument'])&&values['pay.instrument']==='USDT';
  const paysThb=['THB_CASH','THB_BANK'].includes(values['pay.instrument'])&&values['receive.instrument']==='USDT';
  if(!receivesThb&&!paysThb)return null;
  const thb=positiveDecimal(values[(receivesThb?'receive':'pay')+'.quantity']);
  const usdt=positiveDecimal(values[(receivesThb?'pay':'receive')+'.quantity']);
  const spread=positiveDecimal(values.counterparty_spread);
  if(!thb||!usdt||!spread)return null;
  const rn=thb.numerator*usdt.denominator,rd=thb.denominator*usdt.numerator;
  const net=rn*spread.denominator+(receivesThb?1n:-1n)*spread.numerator*rd;
  if(net<=0n)return null;
  return {amount:ratioText(usdt.numerator*spread.numerator*rd,usdt.denominator*spread.denominator*rn,6),
    rate:ratioText(net,rd*spread.denominator,8,true)};
}
