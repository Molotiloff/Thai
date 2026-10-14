import type { TopMetric } from '@/types/mainDashboard';
import { Icon } from '@/components/ui/Icon';
import { TONE_ICON_CLASS, TONE_TEXT_CLASS } from './toneStyles';
import styles from './TopMetricsGrid.module.css';

export function TopMetricsGrid({ metrics }: { metrics: TopMetric[] }) {
  return (
    <div className={styles.grid}>
      {metrics.map((metric) => (
        <article key={metric.id} className={styles.card}>
          <div className={styles.head}>
            <span className={`${styles.iconWrap} ${TONE_ICON_CLASS[metric.tone]}`}>
              <Icon name={metric.icon} size={14} />
            </span>
            <span className={styles.title}>{metric.title}</span>
          </div>

          <span className={styles.value}>{metric.value}</span>

          {metric.subtitleValue !== undefined ? (
            <span className={styles.subtitle}>
              {metric.subtitleLabel ? (
                <span className={styles.subtitleLabel}>{metric.subtitleLabel}</span>
              ) : null}
              <span
                className={
                  metric.tone === 'green' || metric.tone === 'red'
                    ? TONE_TEXT_CLASS[metric.tone]
                    : styles.subtitleValue
                }
              >
                {metric.subtitleValue}
              </span>
            </span>
          ) : null}
        </article>
      ))}
    </div>
  );
}
