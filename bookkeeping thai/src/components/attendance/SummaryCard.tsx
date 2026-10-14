import { Icon } from '@/components/ui/Icon';
import type { SummaryMetric, SummaryTone } from '@/types/attendance';
import styles from './SummaryCard.module.css';

const TONE_CLASS: Record<SummaryTone, string> = {
  blue: styles.toneBlue,
  green: styles.toneGreen,
  orange: styles.toneOrange,
  red: styles.toneRed,
};

export function SummaryCard({ metric }: { metric: SummaryMetric }) {
  return (
    <article className={styles.card}>
      <span className={`${styles.iconWrap} ${TONE_CLASS[metric.tone]}`}>
        <Icon name={metric.icon} size={19} />
      </span>
      <span className={styles.title}>{metric.title}</span>
      <span className={styles.value}>{metric.value}</span>
      <span className={styles.subtitle}>{metric.subtitle}</span>
    </article>
  );
}
