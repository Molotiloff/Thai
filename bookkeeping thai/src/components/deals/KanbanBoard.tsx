import Link from 'next/link';
import {STATUSES,type Workspace} from '@/thai/deals/model';
import {KanbanCard} from './KanbanCard';
import kanban from './KanbanBoard.module.css';
import styles from '@/thai/deals/deals.module.css';
import type {DealFilters} from './DealsHeader';
export function KanbanBoard({data,query,kind,limit}:{data:Workspace}&Pick<DealFilters,'query'|'kind'|'limit'>){
 return (    <div className={kanban.scroll}><div className={`${kanban.board} ${styles.board}`}>
      {STATUSES.map(s=>{
        const entries=data.board.filter(d=>d.status===s.code);const count=data.summaries.find(v=>v.status===s.code)?.count??0;
        return <section className={kanban.column} key={s.code} aria-label={s.label}>
          <header className={`${kanban.columnHeader} ${kanban[s.tone]}`}><span className={kanban.columnTitle}>{s.label}</span><span className={kanban.columnCount}>{count}</span></header>
          {entries.map(e=><KanbanCard key={e.entry_id} deal={e}/>)}
          {!entries.length&&<span className={kanban.empty}>Нет сделок</span>}
          {count>entries.length&&<Link className={styles.link} href={'/deals?'+new URLSearchParams({query,kind,status:s.code,limit})}>Показаны {entries.length} из {count} · открыть реестр →</Link>}
        </section>;
      })}
    </div></div>
 );
}
