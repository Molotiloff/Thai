import type {ReactNode} from 'react';
import styles from './DealDataCard.module.css';
import adaptation from '@/thai/deals/deals.module.css';
export function DealDataCard({title,children,note}:{title:string;children:ReactNode;note?:string}){
 return <section className={styles.card}><h2 className={styles.heading}>Данные сделки · {title}</h2>{children}{note&&<p className={adaptation.hint}>{note}</p>}</section>;
}
