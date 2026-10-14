import type { DailyIndicator, MetricTone } from '@/types/mainDashboard';
import { Icon, type IconName } from '@/components/ui/Icon';
import { TONE_ICON_CLASS, TONE_TEXT_CLASS } from './toneStyles';
import styles from './DailyIndicatorsCard.module.css';

const INDICATOR_ICONS: Record<string, IconName> = {
  income: 'trend-up',
  expense: 'expenses',
  profit: 'coins',
  turnover: 'reports',
  gap: 'alert',
};

export function DailyIndicatorsCard({ indicators }: { indicators: DailyIndicator[] }) {
  return (
    <section className={styles.card}>
      <h2 className={styles.heading}>Показатели за день</h2>
      <dl className={styles.rows}>
        {indicators.map((indicator) => (
          <div key={indicator.id} className={styles.row}>
            <span className={`${styles.iconWrap} ${TONE_ICON_CLASS[indicator.tone]}`}>
              <Icon name={INDICATOR_ICONS[indicator.id] ?? 'coins'} size={13} />
            </span>
            <dt className={styles.label}>{indicator.label}</dt>
            <dd className={`${styles.value} ${valueClass(indicator.tone)}`}>{indicator.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function valueClass(tone: MetricTone): string {
  return tone === 'green' || tone === 'red' ? TONE_TEXT_CLASS[tone] : '';
}
