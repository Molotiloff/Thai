import type { ExpenseMetric } from '@/types/expenses';
import { formatRub } from '@/lib/format';
import { Icon } from '@/components/ui/Icon';
import styles from './ExpenseMetricGrid.module.css';

const TONE_CLASS: Record<ExpenseMetric['tone'], string> = {
  blue: styles.toneBlue,
  orange: styles.toneOrange,
  green: styles.toneGreen,
  purple: styles.tonePurple,
  gray: styles.toneGray,
};

export function ExpenseMetricGrid({ metrics }: { metrics: ExpenseMetric[] }) {
  return (
    <div className={styles.grid}>
      {metrics.map((metric) => (
        <article key={metric.id} className={styles.card}>
          <div className={styles.head}>
            <span className={`${styles.iconWrap} ${TONE_CLASS[metric.tone]}`}>
              <Icon name={metric.icon} size={14} />
            </span>
            <span className={styles.label}>{metric.title}</span>
          </div>
          <span className={styles.value}>{formatRub(metric.value)}</span>
        </article>
      ))}
    </div>
  );
}
