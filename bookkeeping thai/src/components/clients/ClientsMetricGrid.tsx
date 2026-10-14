import type { ClientMetric } from '@/types/clients';
import { Icon } from '@/components/ui/Icon';
import styles from './ClientsMetricGrid.module.css';

const TONE_CLASS: Record<ClientMetric['tone'], string> = {
  blue: styles.toneBlue,
  green: styles.toneGreen,
  orange: styles.toneOrange,
  purple: styles.tonePurple,
};

export function ClientsMetricGrid({ metrics }: { metrics: ClientMetric[] }) {
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
          <span className={styles.value}>{metric.value}</span>
          <span className={styles.delta}>
            <span className={styles.deltaNote}>{metric.subtitle}</span>
          </span>
        </article>
      ))}
    </div>
  );
}
