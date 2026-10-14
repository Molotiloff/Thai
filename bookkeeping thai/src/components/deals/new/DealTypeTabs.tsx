'use client';
import {Icon} from '@/components/ui/Icon';
import {OPERATIONS,type OperationType} from '@/thai/deals/model';
import styles from './DealTypeTabs.module.css';
import adaptation from '@/thai/deals/deals.module.css';
const tones=[styles.tone1,styles.tone2,styles.tone3,styles.tone4];
export function DealTypeTabs({active,onChange,disabled=false}:{active:OperationType;onChange:(type:OperationType)=>void;disabled?:boolean}){
 return <div className={styles.scroll} role="tablist" aria-label="Тип сделки"><div className={`${styles.tabs} ${adaptation.tabs}`}>{OPERATIONS.map((item,index)=><button disabled={disabled} key={item.type} type="button" role="tab" aria-selected={active===item.type} className={`${styles.tab} ${active===item.type?styles.tabActive:''}`} onClick={()=>onChange(item.type)}><span className={`${styles.iconWrap} ${active===item.type?'':tones[index%tones.length]}`}><Icon name={item.icon} size={15}/></span><span className={styles.tabLabel}>{item.label}</span></button>)}</div></div>;
}
