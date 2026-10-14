import Link from 'next/link';
import {operationLabel,entryAmount,timestamp,type DealEntry} from '@/thai/deals/model';
import kanban from './KanbanBoard.module.css';
export function KanbanCard({deal:e}:{deal:DealEntry}){
 return (<Link href={e.href} className={kanban.card}><span className={kanban.cardTop}><span className={kanban.cardId}>{e.deal_id?'#'+e.deal_id:'ОП-'+e.operation_id}</span><span className={kanban.cardClient}>{e.client_name}</span></span><span className={kanban.cardOperation}>{operationLabel(e.kind)}</span><span className={kanban.cardAmount}>{entryAmount(e)}</span><span className={kanban.cardFooter}><span>{e.attributes.mode==='wallet'?'Кошелёк':'Касса / учёт'}</span><span>{timestamp(e.created_at)}</span></span></Link> );
}
