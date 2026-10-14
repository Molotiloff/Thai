import type { TurnoverMetric } from '@/types/turnover';
import { formatPercent } from '@/lib/format';
import { Icon } from '@/components/ui/Icon';
import styles from './TurnoverMetricGrid.module.css';

const TONE_CLASS: Record<TurnoverMetric['tone'], string> = {
  purple: styles.tonePurple,
  blue: styles.toneBlue,
  green: styles.toneGreen,
  orange: styles.toneOrange,
};

export function TurnoverMetricGrid({ metrics }: { metrics: TurnoverMetric[] }) {
  return (
    <div className={styles.grid}>
      {metrics.map((metric) => (
        <article key={metric.id} className={styles.card}>
          <span className={`${styles.iconWrap} ${TONE_CLASS[metric.tone]}`}>
            <Icon name={metric.icon} size={14} />
          </span>
          <span className={styles.label}>{metric.label}</span>
          <span className={styles.value}>{metric.valueText}</span>
          <span className={styles.delta}>
            <span className={metric.changePercent >= 0 ? styles.deltaUp : styles.deltaDown}>
              {formatPercent(metric.changePercent)}
            </span>
            <span className={styles.deltaNote}>{metric.changeNote}</span>
          </span>
        </article>
      ))}
    </div>
  );
}
