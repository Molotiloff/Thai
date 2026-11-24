import Link from 'next/link';
import type { components } from '@/api/openapi';
import {activeClientOptions,apiGet,session} from '../server';
import {OperationForms} from '../forms';
import {expenseForm,expenseActions,expenseBindingForms} from '../form-specs';
import {can,named} from '../types';
import {Page,Panel,Table,PeriodFilter,More,param,period,type Params,type Column,opColumn,Notice} from '../ui';
type Schemas = components['schemas'];

const shareColumns = [
  {key:'name',title:'Участник'},
  {key:'expense_share_usdt',title:'Доля расходов, USDT',amount:true},
  {key:'personally_paid_usdt',title:'Оплачено лично, USDT',amount:true},
] satisfies Column<keyof Schemas['ExpenseShare']>[];

const balanceColumns = [
  {key:'name',title:'Участник',href:'/clients/{client_id}'},
  {key:'payable',title:'Должен фирме',amount:true},
  {key:'receivable',title:'Фирма должна',amount:true},
  {key:'wallet_balance',title:'Баланс USDT',amount:true},
] satisfies Column<keyof Schemas['ExpenseWalletBalance']>[];

export default async function Expenses({searchParams}:{searchParams:Promise<Params>}){
 const q=await searchParams;const p=period(q);const before=param(q,'before_id');const participant=param(q,'participant_id');const currency=param(q,'currency');
 const filter=new URLSearchParams();if(participant)filter.set('participant_id',participant);if(currency)filter.set('currency',currency);
 const [data,debt,categories,participants,statement,revisions,user]=await Promise.all([
   apiGet<Schemas['ExpensePage']>('/expenses?limit=50&period='+p+'&'+filter+(before?'&before_id='+encodeURIComponent(before):'')),
   apiGet<Schemas['ExpenseDebts']>('/expense-debts?'+filter),
   apiGet<Schemas['ExpenseCategory'][]>('/expense-categories'),
   apiGet<Schemas['ExpenseParticipant'][]>('/expense-participants'),
   apiGet<Schemas['ExpenseStatement']>(`/expense-statements?period=${p}`),
   apiGet<Schemas['ExpenseRevision'][]>('/expense-periods/revisions?period='+p),
   session(),
 ]);
 const unbound=(['skyex','pe'] as const).filter(code=>!participants.some(p=>p.code===code&&p.client_id!==null));
 const bindings=unbound.length&&can(user,'expense.post')
   ?expenseBindingForms(await activeClientOptions('partner'),unbound)
   :[];
 const forms=[...bindings,expenseForm(categories.map(r=>[String(r.id),String(r.name)] as const)),...expenseActions];
 return <Page title="Расходы" subtitle="Кто оплатил, чья доля и сколько нужно компенсировать. Взаиморасчёты в USDT через существующие кошельки."><PeriodFilter value={p} extra={<><label>Участник<select name="participant_id" defaultValue={participant}><option value="">Все</option>{participants.map(r=><option key={String(r.party_id)} value={String(r.party_id)}>{String(r.name)}</option>)}</select></label><label>Валюта<select name="currency" defaultValue={currency}><option value="">Все</option>{['THB','USDT','USD','EUR','RUB'].map(c=><option key={c}>{c}</option>)}</select></label></>}/><Notice>Личная оплата начисляется плательщику полностью в USDT. Доля Данияра списывается сразу. Возврат денег оформляется выдачей в карточке клиента. Текущий баланс включает также BestChange и прочие операции; выписка показывает их отдельно.</Notice>
 <Panel title="Текущие взаиморасчёты по расходам">
  <p>Доли и личные оплаты по всем действующим расходам в USDT.</p>
  <Table data={debt.expense_shares} columns={shareColumns}/>
 </Panel>
 <Panel title="Балансы кошельков USDT"><Table data={debt.wallet_balances} columns={balanceColumns}/></Panel>{debt.totals.length>0&&<Panel title="Исторические обязательства"><Table data={named(debt.totals,participants)} columns={[{key:'debtor_name',title:'Должник'},{key:'creditor_name',title:'Получатель'},{key:'currency_code',title:'Валюта'},{key:'amount',title:'Сумма',amount:true}]}/></Panel>}<Panel title="Расходы и расчёты"><OperationForms session={user} specs={forms}/></Panel>
 <Panel title="История расходов"><Table data={named(data.items,participants)} columns={[{key:'id',title:'Расход',href:'/expenses/{id}'},opColumn,{key:'business_date',title:'Дата'},{key:'payer_name',title:'Плательщик'},{key:'currency_code',title:'Валюта'},{key:'cash_instrument_code',title:'Кассовый счёт'},{key:'amount',title:'Сумма',amount:true},{key:'business_usdt',title:'Расход бизнеса USDT',amount:true},{key:'comment',title:'Комментарий'},{key:'active',title:'Действует'}]}/><More href={`/expenses?period=${p}&${filter}`} cursor={data.next_before_id} name="before_id"/></Panel>
 <Panel title={`Выписка за ${p.slice(0,7)} · ${statement.closed?'закрыта':'текущая'}`}>
 {statement.needs_revision?<Notice>Изменена предыдущая выписка. Проверьте остатки; для корректировки создайте отдельную операцию в открытом периоде.</Notice>:null}<Table data={named(statement.rows,participants)} columns={[{key:'debtor_name',title:'Должник'},{key:'creditor_name',title:'Получатель'},{key:'currency_code',title:'Валюта'},{key:'opening',title:'На начало',amount:true},{key:'accrued',title:'Компенсация расходов',amount:true},{key:'withheld',title:'Удержано по расходам',amount:true},{key:'other_movements',title:'Прочие зачисления',amount:true},{key:'settled',title:'Выдачи и прочие списания',amount:true},{key:'netted',title:'Взаимозачёт',amount:true},{key:'corrected',title:'Корректировки',amount:true},{key:'closing',title:'На конец',amount:true}]}/></Panel>
 <Panel title="История исправлений выписки"><Table data={revisions} columns={[opColumn,{key:'reason',title:'Причина'},{key:'completed_by_operation_id',title:'Завершена операцией',href:'/operations/{completed_by_operation_id}'}]}/></Panel></Page>
}
export async function ExpenseDetail({params}:{params:Promise<{id:string}>}){
 const {id}=await params;const [data,parties]=await Promise.all([apiGet<Schemas['ExpenseDetail']>('/expenses/'+encodeURIComponent(id)),apiGet<Schemas['ExpenseParticipant'][]>('/expense-participants')]);
 return <Page back={{href:"/expenses",label:"Расходы"}} title={`Расход #${id}`} subtitle={String(data.comment??'Распределение расхода')}><Panel title="Документ"><Table data={named([data],parties)} columns={[opColumn,{key:'business_date',title:'Дата'},{key:'currency_code',title:'Валюта'},{key:'cash_instrument_code',title:'Кассовый счёт'},{key:'amount',title:'Сумма',amount:true},{key:'quoted_rate',title:'Курс',amount:true},{key:'payer_name',title:'Плательщик'},{key:'cancelled',title:'Отменён'}]}/></Panel><Panel title="Итоговые доли"><Table data={named(data.allocations,parties)} columns={[{key:'party_name',title:'Участник'},{key:'percent',title:'Доля %',amount:true},{key:'amount',title:'Сумма',amount:true},{key:'amount_usdt',title:'Оценка USDT',amount:true}]}/></Panel><Panel title="Движения кошельков USDT"><Table data={data.wallet_entries} columns={[{key:'participant_code',title:'Участник'},{key:'client_id',title:'Кошелёк клиента',href:'/clients/{client_id}'},{key:'credit_usdt',title:'За оплату +USDT',amount:true},{key:'debit_usdt',title:'Доля расхода −USDT',amount:true}]}/></Panel><Panel title="Исторические обязательства"><Table data={named(data.obligations,parties)} columns={[{key:'debtor_name',title:'Должник'},{key:'creditor_name',title:'Кредитор'},{key:'amount',title:'Начислено',amount:true},{key:'currency_code',title:'Валюта'}]}/><Link href="/expenses">К расходам и расчётам →</Link></Panel></Page>
}
