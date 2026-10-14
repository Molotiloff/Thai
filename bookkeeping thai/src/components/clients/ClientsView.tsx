'use client';
import { useMemo,useState } from 'react';
import type { ClientsPageData } from '@/types/clients';
import { can,type Session } from '@/thai/types';
import { ActionForm } from '@/thai/forms';
import { ClientsHeader } from './ClientsHeader';
import { ClientsMetricGrid } from './ClientsMetricGrid';
import { ClientsTable } from './ClientsTable';
import { ClientInfoPanel } from './ClientInfoPanel';
import styles from './ClientsView.module.css';

export function ClientsView({data,session,initialClientId}:{data:ClientsPageData;session:Session;initialClientId?:string}){
 const [query,setQuery]=useState('');const [selectedId,setSelectedId]=useState<string|null>(initialClientId??data.clients[0]?.id??null);
 const [creating,setCreating]=useState(false);const [filters,setFilters]=useState(false);const [group,setGroup]=useState('');const [status,setStatus]=useState('');
 const filtered=useMemo(()=>data.clients.filter(c=>{
  const q=query.trim().toLowerCase();return (!q||[c.name,c.telegramUsername,c.telegramChatId,c.clientNumber].some(v=>v.toLowerCase().includes(q)))&&(!group||c.groupCode===group)&&(!status||(status==='active'?c.isActive:c.isActive===false));
 }),[data.clients,query,group,status]);
 const selected=filtered.find(c=>c.id===selectedId)??null;
 return <>
  <ClientsHeader query={query} onQueryChange={setQuery} onCreate={()=>setCreating(true)} onToggleFilters={()=>setFilters(!filters)} canCreate={can(session,'client.manage')}/>
  <ClientsMetricGrid metrics={data.metrics}/>
  {filters?<div className={styles.filters}><label>Группа <select value={group} onChange={e=>setGroup(e.target.value)}><option value="">Все группы</option><option value="balance">Баланс</option><option value="partner">Партнер</option></select></label><label>Статус <select value={status} onChange={e=>setStatus(e.target.value)}><option value="">Все клиенты</option><option value="active">Активные</option><option value="closed">Закрытые</option></select></label></div>:null}
  <div className={`${styles.grid} ${selected?'':styles.gridNoPanel}`}>
   <ClientsTable key={`${query}:${group}:${status}`} clients={filtered} totalClients={filtered.length} selectedId={selectedId} onSelect={setSelectedId}/>
   {selected?<ClientInfoPanel key={selected.id} client={selected} onClose={()=>setSelectedId(null)} session={session} canEditTelegramLink={!!selected.telegramChatId&&selected.isActive!==false&&can(session,'client.manage')}/>:null}
  </div>
  {creating?<dialog ref={node=>{if(node&&!node.open)node.showModal();}} onCancel={()=>setCreating(false)} aria-labelledby="new-client-title" className={styles.dialog}><header><h2 id="new-client-title">Новый клиент</h2><button type="button" onClick={()=>setCreating(false)} aria-label="Закрыть">×</button></header><ActionForm submitLabel="Создать клиента" onSuccess={result=>{setSelectedId(String(result.client_id));setQuery('');setGroup('');setStatus('');setCreating(false);}} spec={{title:'Новый клиент',path:'/clients',permission:'client.manage',financial:false,fields:[{name:'name',label:'Имя клиента'},{name:'group_code',label:'Группа',options:[['balance','Баланс'],['partner','Партнер']]},{name:'chat_id',label:'Telegram Chat ID',required:false}]}}/></dialog>:null}
 </>;
}
