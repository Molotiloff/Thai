import type { CitySummary } from '@/types/mainDashboard';
import { Icon, type IconName } from '@/components/ui/Icon';
import { TONE_TEXT_CLASS } from './toneStyles';
import styles from './CitySummariesGrid.module.css';

const CITY_ICONS: Readonly<Record<string, IconName>> = {
  ЕКБ: 'building',
  ЧЛБ: 'home',
  МСК: 'trend-up',
};

export function CitySummariesGrid({ summaries }: { summaries: CitySummary[] }) {
  return (
    <div className={styles.grid}>
      {summaries.map((summary) => (
        <section key={summary.id} className={styles.card}>
          <div className={styles.head}>
            <span className={styles.iconWrap}>
              <Icon name={CITY_ICONS[summary.city] ?? 'building'} size={14} />
            </span>
            <h2 className={styles.heading}>{summary.city}</h2>
          </div>

          <dl className={styles.rows}>
            {summary.rows.map((row, index) => (
              <div key={`${row.label}-${index}`} className={styles.row}>
                <dt className={styles.label}>{row.label}</dt>
                <dd className={`${styles.value} ${row.tone ? TONE_TEXT_CLASS[row.tone] : ''}`}>
                  {row.value}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}
