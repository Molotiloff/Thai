'use client';
import { useRef,useState,useSyncExternalStore,type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { Icon } from '@/components/ui/Icon';
import { can,type Session } from './types';
import styles from './thai.module.css';
import type { FormSpec } from './form-specs';
function assign(target:Record<string,unknown>,path:string,value:unknown) { const names=path.split('.');let obj=target;for(const key of names.slice(0,-1)){obj[key]??={};obj=obj[key] as Record<string,unknown>;}obj[names.at(-1)!]=value; }
function percent(value:string):bigint {if(!/^\d+(\.\d{1,6})?$/.test(value)) throw new Error('Доли: неотрицательные проценты, до 6 знаков после точки');const [i,d='']=value.split('.');return BigInt(i)*1000000n+BigInt(d.padEnd(6,'0'));}
function build(spec:FormSpec,form:HTMLFormElement) {
  const data=new FormData(form);const body:Record<string,unknown>={};let path=spec.path;
  for(const f of spec.fields){if(f.transient)continue;let value=data.get(f.name);if(f.kind==='checkbox') value=data.has(f.name)?'true':'false';if(value===null||value==='')continue;const str=String(value).trim().replace(f.kind==='money'?',':'\u0000','.');let v:unknown=str;
    if(f.name==='chat_id'){v=Number(str);if(!Number.isSafeInteger(v)||v===0)throw new Error('Укажите целый ненулевой Chat ID');}
    if(f.kind==='id'){v=Number(str);if(!Number.isSafeInteger(v)||Number(v)<=0)throw new Error(`${f.label}: укажите положительный целый ID`);}
    if(f.kind==='checkbox')v=str==='true';if(f.kind==='month')v=str+'-01';
    if(path.includes('{'+f.name+'}')){path=path.replace('{'+f.name+'}',encodeURIComponent(str));continue;}
    assign(body,f.name,v);
  }
  if(spec.shares){const shares=body.shares as Record<string,string>;if(Object.values(shares).reduce((s,v)=>s+percent(v),0n)!==100000000n)throw new Error('Сумма долей должна составлять 100%');}
  return {path,body};
}
function responseError(result:unknown,spec:FormSpec,status:number):string {
  if(!result||typeof result!=='object'||!('error' in result))return `Не удалось провести операцию (HTTP ${status}). Повторите запрос.`;
  const error=result.error as {message?:string;fields?:{path:(string|number)[];type:string}[]};
  if(!error||typeof error!=='object')return 'Операция отклонена';
  const fields=error.fields?.map(field=>{
    const name=field.path.filter(part=>part!=='body').join('.');
    const label=spec.fields.find(item=>item.name===name)?.label??name;
    return `${label}: ${field.type==='missing'?'заполните поле':'проверьте значение'}`;
  });
  return [error.message??'Операция отклонена',...(fields??[])].join('\n');
}
export function OperationForms({specs,session}:{specs:FormSpec[];session:Session}) {
  const allowed=specs.filter(spec=>can(session,spec.permission));
  if(!allowed.length)return <p className={styles.notice}>Для вашей роли доступен просмотр этого раздела.</p>;
  return <div className={styles.forms}>{allowed.map(spec=><details key={spec.title} data-tone={spec.permission==='finance.reverse'?'danger':undefined}>
    <summary><span><Icon name={spec.permission==='finance.reverse'?'x-circle':'plus'} size={15}/>{spec.title}</span><Icon name="chevron-down" size={16}/></summary>
    {spec.note&&<p className={styles.notice}>{spec.note}</p>}<ActionForm spec={spec}/>
  </details>)}</div>;
}
const subscribeHydration=()=>()=>{};
const clientReady=()=>true;
const serverReady=()=>false;
export function ActionForm({spec,formId,className,submitLabel,onValuesChange,deriveValues,onBusyChange,onSuccess}:{spec:FormSpec;formId?:string;className?:string;submitLabel?:string;onValuesChange?:(values:Record<string,string>)=>void;deriveValues?:(values:Record<string,string>,changed:string)=>Record<string,string>;onBusyChange?:(busy:boolean)=>void;onSuccess?:(result:Record<string,unknown>)=>void}) {
  const hydrated=useSyncExternalStore(subscribeHydration,clientReady,serverReady);
  const router=useRouter();const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [success,setSuccess]=useState('');const [preview,setPreview]=useState(spec.shares?'Итоговые доли: Данияр 0%, ПЕ 50%, SkyEX 50%':'');const [commonEnabled,setCommonEnabled]=useState(true);
  const attempt=useRef<{source:string;body:Record<string,unknown>}|null>(null);const lock=useRef(false);
  async function submit(event:FormEvent<HTMLFormElement>){event.preventDefault();if(lock.current||success)return;setError('');setSuccess('');let request;
    try {request=build(spec,event.currentTarget);}catch(err){setError(err instanceof Error?err.message:'Проверьте значения');return;}
    try {
      const source=JSON.stringify(request);
      if(attempt.current?.source!==source)attempt.current={source,body:{...request.body,idempotency_key:crypto.randomUUID(),...(spec.financial===false?{}:{effective_at:new Date().toISOString()})}};
    } catch {setError('Не удалось подготовить операцию. Откройте CRM по HTTPS или на localhost.');return;}
    lock.current=true;setBusy(true);onBusyChange?.(true);
    try {const response=await fetch('/api/thai'+request.path,{method:spec.method??'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(attempt.current.body)});const result=await response.json().catch(()=>null);if(response.status===401){router.push('/login');return;}if(!response.ok)throw new Error(responseError(result,spec,response.status));if(!result||typeof result!=='object')throw new Error('Сервер вернул некорректный ответ. Повторите запрос.');setSuccess(`Готово${result.id?' · операция #'+result.id:''}${result.client_id?' · клиент #'+result.client_id:''}${result.deal_id?' · заявка #'+result.deal_id:''}`);router.refresh();onSuccess?.(result);}
    catch(err){setError(err instanceof Error?err.message:'Сеть недоступна. Повтор сохранит ключ операции.');}
    finally{lock.current=false;setBusy(false);onBusyChange?.(false);}
  }
  function update(event:FormEvent<HTMLFormElement>){setSuccess('');
    const form=event.currentTarget;
    const values=Object.fromEntries([...new FormData(form).entries()].map(([key,value])=>[key,String(value)]));
    const changed=(event.target as HTMLInputElement|HTMLSelectElement).name??'';
    for(const [name,value] of Object.entries(deriveValues?.(values,changed)??{})){
      const field=form.elements.namedItem(name);
      if(field instanceof HTMLInputElement||field instanceof HTMLSelectElement)field.value=value;
    }
    onValuesChange?.(Object.fromEntries([...new FormData(form).entries()].map(([key,value])=>[key,String(value)])));
    if(!spec.shares)return;try{const d=new FormData(event.currentTarget);const get=(key:string)=>percent(String(d.get('shares.'+key)||'0').replace(',','.'));const common=d.has('_use_common')?get('common'):0n;const fmt=(v:bigint)=>`${v/2000000n}.${String(v%2000000n*5n).padStart(7,'0')}`.replace(/\.?0+$/,'');setPreview(`Итоговые доли: Данияр ${fmt(get('daniyar')*2n)}%, ПЕ ${fmt(get('pe')*2n+common)}%, SkyEX ${fmt(get('skyex')*2n+common)}%`);}catch{setPreview('Проверьте проценты');}}
  return <form id={formId} onSubmit={submit} onInput={update} method="post" action="/api/thai/unhydrated" className={`${styles.form} ${className??''}`}><fieldset className={styles.fields} disabled={!hydrated||busy}>{spec.shares&&<label><input type="checkbox" name="_use_common" checked={commonEnabled} onChange={event=>setCommonEnabled(event.target.checked)}/>Общая касса</label>}{spec.fields.map(f=><label key={f.name}>{f.label}{f.options?<select name={f.name} required={f.required!==false} defaultValue={f.value??f.options[0]?.[0]}>{!f.required&&f.required===false&&<option value="">Не задано</option>}{f.options.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>:<input name={f.name} type={f.kind==='date'?'date':f.kind==='month'?'month':f.kind==='checkbox'?'checkbox':'text'} required={f.required!==false&&f.kind!=='checkbox'} inputMode={f.kind==='money'?'decimal':f.kind==='id'?'numeric':undefined} disabled={f.name==='shares.common'&&!commonEnabled} defaultValue={f.value} readOnly={f.readOnly} autoComplete="off"/>}</label>)}<button disabled={busy||!!success}>{busy?'Провожу…':success?'Проведено':submitLabel??'Провести'}</button>{success&&<button type="button" onClick={()=>{attempt.current=null;setSuccess('');}}>Новая операция</button>}</fieldset>{preview&&<p className={styles.preview}>{preview}</p>}{error&&<p role="alert" className={styles.error}>{error}</p>}{success&&<p role="status" className={styles.success}>{success}</p>}</form>
}
