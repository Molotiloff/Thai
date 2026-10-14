import type { Client, ClientsPageData } from '@/types/clients';
import { initialsOf } from '@/lib/avatar';
import { cookies } from 'next/headers';
import { apiGet, baseUrl, COOKIE } from '@/thai/server';
import { ApiClient } from '@/api/httpClient';
import type { IClientsService } from './interfaces';
import { labels } from '@/thai/types';

type Entry = { entry_id: string; kind: string; href: string; attributes: { receive?: {instrument:string}; pay?: {instrument:string} }; amount_usdt: string | null; created_at: string; status: string };
type Customer = { id:number; name:string; created_at:string; is_active:boolean; group_code:string; group_name:string; telegram_chat_id:number|null; invite_link:string|null; wallets:{instrument_code:string; balance:string; unvalued_quantity?:string}[]; stats:{operations_count?:number; exchanges_count?:number; turnover_usdt?:string; purchase_usdt?:string; sale_usdt?:string; manager?:string}; recent:Entry[] };
const operationNames: Record<string, Client['recentDeals'][number]['type']> = {sale:'Продажа',purchase:'Покупка',conversion:'Конвертация',best_change:'BestChange',deposit:'Пополнение',withdrawal:'Списание',client_transfer:'Перевод',thb_transfer:'Перевод'};
const date = (value:string) => new Date(value).toLocaleDateString('ru-RU',{timeZone:'Asia/Bangkok'});
function adaptClient(row:Customer):Client {
 const turnover=Number(row.stats.turnover_usdt??0);const count=row.stats.operations_count??0;
 return {id:String(row.id),clientNumber:`#${row.id}`,name:row.name,initials:initialsOf(row.name),
  telegramUsername:row.telegram_chat_id?'Telegram-чат':'Не привязан',telegramChatId:row.telegram_chat_id===null?'':String(row.telegram_chat_id),telegramInviteLink:row.invite_link,
  groupCode:row.group_code,groupName:row.group_name,isActive:row.is_active,
  dealsCount:count,turnoverUsdt:turnover,managerName:row.stats.manager??'—',registrationDate:date(row.created_at),
  balances:row.wallets.map(w=>({currency:w.instrument_code,amount:Number(w.balance),unvaluedAmount:Number(w.unvalued_quantity??0)})),totalProfitUsdt:0,
  purchaseVolumeUsdt:Number(row.stats.purchase_usdt??0),saleVolumeUsdt:Number(row.stats.sale_usdt??0),averageCheckUsdt:row.stats.exchanges_count?turnover/row.stats.exchanges_count:0,
  recentDeals:row.recent.map(e=>({id:e.entry_id,type:operationNames[e.kind]??'Конвертация',direction:[e.attributes.receive?.instrument,e.attributes.pay?.instrument].filter(Boolean).map(i=>labels[i!]??i).join(' → '),amountUsdt:Number(e.amount_usdt??0),time:date(e.created_at),href:e.href,status:e.status})),comments:[]};
}
export class ClientsService implements IClientsService {
 async getClientsPage():Promise<ClientsPageData>{
  const data=await apiGet<{clients:Customer[]}>('/clients/workspace');const clients=data.clients.map(adaptClient);
  return {clients,totalClients:clients.length,metrics:[
   {id:'total',title:'Всего клиентов',value:clients.length,subtitle:'Клиентская база',tone:'purple',icon:'clients',changePercent:0},
   {id:'active',title:'С ненулевым балансом',value:clients.filter(c=>c.isActive&&c.balances.some(b=>b.amount!==0)).length,subtitle:'По восьми счетам',tone:'green',icon:'check-circle',changePercent:0},
   {id:'partners',title:'Партнеры',value:clients.filter(c=>c.isActive&&c.groupCode==='partner').length,subtitle:'Балансы во вкладке «Займы»',tone:'blue',icon:'clients',changePercent:0},
   {id:'operations',title:'Проведено операций',value:clients.reduce((v,c)=>v+c.dealsCount,0),subtitle:'Без отменённых операций',tone:'orange',icon:'deals',changePercent:0},
   {id:'turnover',title:'Оборот обменов',value:clients.reduce((v,c)=>v+c.turnoverUsdt,0).toLocaleString('ru-RU',{maximumFractionDigits:2})+' USDT',subtitle:'Историческая оценка входа',tone:'purple',icon:'coins',changePercent:0},
  ]};
 }
 async getClientById(id:string):Promise<Client|null>{const data=await apiGet<{clients:Customer[]}>('/clients/workspace?client_id='+encodeURIComponent(id));return data.clients[0]?adaptClient(data.clients[0]):null;}
 async updateTelegramInviteLink(id:string,inviteLink:string|null):Promise<Client>{
  const token=(await cookies()).get(COOKIE)?.value;
  if(!token)throw new Error('Сессия входа истекла');
  await new ApiClient({baseUrl:baseUrl(),getToken:()=>token}).request(`/clients/${encodeURIComponent(id)}/telegram-link`,{method:'PUT',body:JSON.stringify({invite_link:inviteLink,idempotency_key:crypto.randomUUID()})});
  const client=await this.getClientById(id);if(!client)throw new Error('Клиент не найден');return client;
 }
}

