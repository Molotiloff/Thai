import styles from '@/thai/thai.module.css';

export default function Loading() {
  return <section className={styles.loading} role="status" aria-live="polite" aria-busy="true">
    <p>Загружаем данные…</p>
    <div className={styles.loadingMetrics} aria-hidden="true">{[0,1,2,3].map(item=><div className={styles.loadingCard} key={item}><span/><span/></div>)}</div>
    <div className={styles.loadingPanel} aria-hidden="true">{[0,1,2,3,4].map(item=><span key={item}/>)}</div>
  </section>;
}
