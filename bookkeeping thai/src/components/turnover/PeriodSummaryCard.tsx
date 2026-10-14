import type { PeriodSummary } from '@/types/turnover';
import { formatRub } from '@/lib/format';
import styles from './PeriodSummaryCard.module.css';

export function PeriodSummaryRows({ summary }: { summary: PeriodSummary }) {
  const rows = [
    { label: 'Максимальный оборот', value: formatRub(summary.maxTurnover.value), note: summary.maxTurnover.date },
    { label: 'Минимальный оборот', value: formatRub(summary.minTurnover.value), note: summary.minTurnover.date },
    { label: 'Максимальная прибыль', value: formatRub(summary.maxProfit.value), note: summary.maxProfit.date },
    { label: 'Средняя прибыль в день', value: formatRub(summary.averageDailyProfit) },
    { label: 'Средний чек', value: formatRub(summary.averageCheck) },
  ];

  return (
    <dl className={styles.rows}>
      {rows.map((row) => (
        <div key={row.label} className={styles.row}>
          <dt className={styles.label}>{row.label}</dt>
          <dd className={styles.valueWrap}>
            <span className={styles.value}>{row.value}</span>
            {row.note ? <span className={styles.note}>{row.note}</span> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}
