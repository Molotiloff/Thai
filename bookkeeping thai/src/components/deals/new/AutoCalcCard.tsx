import styles from './AutoCalcCard.module.css';
import adaptation from '@/thai/deals/deals.module.css';
export interface CalcRow {label:string;text:string;}
export function AutoCalcCard({rows,note}:{rows:readonly CalcRow[];note:string}){
 return <section className={styles.card}><h2 className={styles.heading}>Авто расчёт</h2><dl className={adaptation.preview}>{rows.map(row=><div key={row.label}><dt>{row.label}</dt><dd>{row.text}</dd></div>)}</dl><p className={adaptation.hint}>{note}</p></section>;
}
