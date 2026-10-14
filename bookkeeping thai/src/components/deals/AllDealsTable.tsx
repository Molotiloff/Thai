import Link from 'next/link';
import {STATUSES,operationLabel,entryDirection,entryAmount,timestamp,type Workspace} from '@/thai/deals/model';
import table from './AllDealsTable.module.css';
import summary from './DealStatusSummary.module.css';
import styles from '@/thai/deals/deals.module.css';
import type {DealFilters} from './DealsHeader';
export function AllDealsTable({data,query,status,kind,limit}:{data:Workspace}&DealFilters){
 const pageQuery=new URLSearchParams({query,status,kind,limit});
 if(data.next_cursor)pageQuery.set('before',data.next_cursor);
 return (    <section className={table.card}>
      <h2 className={table.heading}>Все сделки</h2><div className={table.scroll}><table className={table.table}><thead><tr><th>ID сделки</th><th>Клиент</th><th>Тип сделки</th><th>Направление</th><th>Сумма</th><th>Статус</th><th>Создано · Бангкок</th><th>Создал</th></tr></thead><tbody>
        {data.items.map(e=><tr key={e.entry_id} className={table.row}><td><Link className={table.link} href={e.href}>{e.deal_id?'#'+e.deal_id:'ОП-'+e.operation_id}</Link></td><td className={table.clientCol}>{e.client_name}</td><td>{operationLabel(e.kind)}</td><td>{entryDirection(e)}</td><td className={table.amountCol}>{entryAmount(e)}</td><td><span className={`${styles.status} ${summary[STATUSES.find(s=>s.code===e.status)?.tone??'fixed']}`}>{STATUSES.find(s=>s.code===e.status)?.label??e.status}</span></td><td>{timestamp(e.created_at)}</td><td>{e.created_by}</td></tr>)}
        {!data.items.length&&<tr><td colSpan={8} className={table.emptyCell}>Ничего не найдено — измените фильтр или запрос.</td></tr>}
      </tbody></table></div>
      <footer className={table.footer}><form action="/deals" className={styles.filters}><input type="hidden" name="query" value={query}/><input type="hidden" name="kind" value={kind}/><input type="hidden" name="status" value={status}/><label>Показать по: <select name="limit" defaultValue={limit} aria-label="Сделок на странице">{['10','20','50','100'].map(n=><option key={n}>{n}</option>)}</select></label><button className={styles.link}>Показать</button></form>{data.next_cursor&&<Link href={'/deals?'+pageQuery} className={table.link}>Следующая страница →</Link>}</footer>
    </section>
 );
}
