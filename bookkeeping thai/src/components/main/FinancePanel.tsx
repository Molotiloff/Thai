import type { FinanceIndicator } from '@/types/mainDashboard';
import styles from './FinancePanel.module.css';

export function FinancePanel({ indicators }: { indicators: FinanceIndicator[] }) {
  return (
    <section className={styles.card}>
      <h2 className={styles.heading}>Финансы</h2>
      <dl className={styles.rows}>
        {indicators.map((indicator) => (
          <div key={indicator.id} className={styles.row}>
            <dt className={styles.label}>{indicator.label}</dt>
            <dd className={styles.valueWrap}>
              <span className={`${styles.value} ${indicator.isNegative ? styles.negative : ''}`}>
                {indicator.value}
              </span>
              {indicator.percent ? <span className={styles.percent}>{indicator.percent}</span> : null}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
