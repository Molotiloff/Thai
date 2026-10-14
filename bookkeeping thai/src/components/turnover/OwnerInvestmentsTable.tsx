import type { OwnerInvestment } from '@/types/turnover';
import { formatRub } from '@/lib/format';
import { OWNER_COLORS, ProgressBar } from './bits';
import styles from './turnoverTables.module.css';

export function OwnerInvestmentsTable({ rows }: { rows: OwnerInvestment[] }) {
  return (
    <div className={styles.scroll}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Владелец</th>
            <th className={styles.num}>Вложено</th>
            <th className={styles.num}>Текущий оборот</th>
            <th className={styles.num}>Прибыль</th>
            <th>% выплаты</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.ownerId}>
              <td className={styles.strong}>
                <span className={styles.dotName}>
                  <span
                    className={styles.dot}
                    style={{ background: OWNER_COLORS[row.ownerId] }}
                    aria-hidden="true"
                  />
                  {row.ownerName}
                </span>
              </td>
              <td className={styles.num}>{formatRub(row.invested)}</td>
              <td className={`${styles.num} ${styles.strong}`}>{formatRub(row.currentTurnover)}</td>
              <td className={`${styles.num} ${row.profit < 0 ? styles.negative : styles.positive}`}>
                {formatRub(row.profit)}
              </td>
              <td className={styles.progressCell}>
                <span className={styles.progressWrap}>
                  <ProgressBar percent={row.payoutPercent} color="var(--delta-up)" />
                  <span className={styles.progressValue}>{row.payoutPercent}%</span>
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
