import {Icon} from '@/components/ui/Icon';
import {STATUSES,type Workspace} from '@/thai/deals/model';
import summary from './DealStatusSummary.module.css';
import styles from '@/thai/deals/deals.module.css';
export function DealStatusSummaryGrid({summaries}:{summaries:Workspace['summaries']}){
 const data={summaries};
 return (    <div className={`${summary.grid} ${styles.summary}`}>
      {STATUSES.map(s=><article className={summary.card} key={s.code}><div className={summary.head}><span className={`${summary.iconWrap} ${summary[s.tone]}`}><Icon name={s.icon} size={14}/></span><span className={summary.label}>{s.label}</span></div><span className={summary.count}>{data.summaries.find(v=>v.status===s.code)?.count??0}</span><span className={summary.sum}>сделок</span></article>)}
    </div>
 );
}
