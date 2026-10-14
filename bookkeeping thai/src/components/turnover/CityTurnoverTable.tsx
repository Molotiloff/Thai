import type { CityTurnoverRow } from '@/types/turnover';
import { formatRub } from '@/lib/format';
import { PercentBadge } from './bits';
import styles from './turnoverTables.module.css';

interface CityTurnoverTableProps {
  rows: CityTurnoverRow[];
  totalChangePercent: number;
}

export function CityTurnoverTable({ rows, totalChangePercent }: CityTurnoverTableProps) {
  const total = rows.reduce(
    (acc, row) => ({
      turnover: acc.turnover + row.turnover,
      purchase: acc.purchase + row.purchase,
      sale: acc.sale + row.sale,
      profit: acc.profit + row.profit,
    }),
    { turnover: 0, purchase: 0, sale: 0, profit: 0 },
  );

  return (
    <div className={styles.scroll}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Город</th>
            <th className={styles.num}>Оборот</th>
            <th className={styles.num}>Покупка</th>
            <th className={styles.num}>Продажа</th>
            <th className={styles.num}>Прибыль</th>
            <th className={styles.num}>Динамика</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.city}>
              <td className={styles.strong}>{row.city}</td>
              <td className={`${styles.num} ${styles.strong}`}>{formatRub(row.turnover)}</td>
              <td className={styles.num}>{formatRub(row.purchase)}</td>
              <td className={styles.num}>{formatRub(row.sale)}</td>
              <td className={styles.num}>{formatRub(row.profit)}</td>
              <td className={styles.num}>
                <PercentBadge value={row.changePercent} />
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className={styles.totalRow}>
            <td>Итого</td>
            <td className={styles.num}>{formatRub(total.turnover)}</td>
            <td className={styles.num}>{formatRub(total.purchase)}</td>
            <td className={styles.num}>{formatRub(total.sale)}</td>
            <td className={styles.num}>{formatRub(total.profit)}</td>
            <td className={styles.num}>
              <PercentBadge value={totalChangePercent} />
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
