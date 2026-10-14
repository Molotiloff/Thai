import type { CurrencyTurnoverRow } from '@/types/turnover';
import { formatRub } from '@/lib/format';
import { ProgressBar } from './bits';
import styles from './turnoverTables.module.css';

export function CurrencyTurnoverTable({ rows }: { rows: CurrencyTurnoverRow[] }) {
  const total = rows.reduce((sum, row) => sum + row.turnover, 0);

  return (
    <div className={styles.scroll}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Валюта</th>
            <th className={styles.num}>Оборот</th>
            <th className={styles.num}>Доля</th>
            <th aria-label="Доля, диаграмма" />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.currency}>
              <td className={styles.strong}>{row.currency}</td>
              <td className={styles.num}>{formatRub(row.turnover)}</td>
              <td className={styles.num}>{row.sharePercent.toFixed(1)}%</td>
              <td className={styles.progressCell}>
                <ProgressBar percent={row.sharePercent} />
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className={styles.totalRow}>
            <td>Итого</td>
            <td className={styles.num}>{formatRub(total)}</td>
            <td className={styles.num}>100%</td>
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
