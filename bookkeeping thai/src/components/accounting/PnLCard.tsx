import type { PnLReport } from '@/types/domain';
import { formatRub } from '@/lib/format';
import { Icon } from '@/components/ui/Icon';
import styles from './PnLCard.module.css';

/** P&L за период с экспортом PDF-отчёта */
export function PnLCard({ report }: { report: PnLReport }) {
  const rows = [
    { label: 'Доходы с клиентов', value: report.clientIncome, sign: '+' },
    { label: 'Постоянные расходы', value: -report.fixedExpenses, sign: '−' },
    { label: 'Переменные расходы', value: -report.variableExpenses, sign: '−' },
  ];

  return (
    <div className={styles.pnl}>
      <ul className={styles.rows}>
        {rows.map((row) => (
          <li key={row.label} className={styles.row}>
            <span className={styles.label}>{row.label}</span>
            <span className={`${styles.value} ${row.value < 0 ? styles.negative : styles.positive}`}>
              {row.sign} {formatRub(Math.abs(row.value))}
            </span>
          </li>
        ))}
      </ul>

      <div className={styles.total}>
        <span className={styles.totalLabel}>Прибыль за период</span>
        <span className={styles.totalValue}>{formatRub(report.profit)}</span>
      </div>

      <button type="button" className={styles.exportButton}>
        <Icon name="reports" size={15} />
        Экспорт PDF-отчёта
      </button>
    </div>
  );
}
