import type { SystemMetric } from '@/types/mainDashboard';
import { Icon, type IconName } from '@/components/ui/Icon';
import { TONE_ICON_CLASS, TONE_TEXT_CLASS } from './toneStyles';
import styles from './SystemMetricsSection.module.css';

const METRIC_ICONS: Record<string, IconName> = {
  yield: 'percent',
  'month-turnover': 'reports',
  'usdt-rest': 'coins',
  gap: 'alert',
  'working-capital': 'refresh',
  rub: 'wallet',
  'rub-in-currency': 'coins',
};

export function SystemMetricsSection({ metrics }: { metrics: SystemMetric[] }) {
  return (
    <section className={styles.card}>
      <h2 className={styles.heading}>Системные показатели</h2>
      <div className={styles.grid}>
        {metrics.map((metric) => (
          <article key={metric.id} className={styles.metric}>
            <div className={styles.metricHead}>
              <span className={`${styles.metricIcon} ${TONE_ICON_CLASS[metric.tone]}`}>
                <Icon name={METRIC_ICONS[metric.id] ?? 'coins'} size={11} />
              </span>
              <span className={styles.metricTitle}>{metric.title}</span>
            </div>
            <span
              className={`${styles.metricValue} ${
                metric.tone === 'red' ? TONE_TEXT_CLASS.red : ''
              }`}
            >
              {metric.value}
            </span>
            <span className={styles.metricSubtitle}>{metric.subtitle}</span>
          </article>
        ))}
      </div>
    </section>
  );
}
