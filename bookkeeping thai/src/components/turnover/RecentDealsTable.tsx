import Link from 'next/link';
import type { RecentDeal } from '@/types/turnover';
import { formatRub } from '@/lib/format';
import { Icon } from '@/components/ui/Icon';
import tableStyles from './turnoverTables.module.css';
import styles from './RecentDealsTable.module.css';

export function RecentDealsTable({ rows }: { rows: RecentDeal[] }) {
  return (
    <div className={styles.wrapper}>
      <div className={tableStyles.scroll}>
        <table className={tableStyles.table}>
          <thead>
            <tr>
              <th>ID сделки</th>
              <th>Клиент</th>
              <th>Тип сделки</th>
              <th>Направление</th>
              <th className={tableStyles.num}>Сумма</th>
              <th>Время</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((deal) => (
              <tr key={deal.id}>
                <td>{deal.id}</td>
                <td className={tableStyles.strong}>{deal.clientName}</td>
                <td>{deal.type}</td>
                <td>{deal.direction}</td>
                <td className={`${tableStyles.num} ${tableStyles.strong}`}>
                  {formatRub(deal.amountRub)}
                </td>
                <td>{deal.time}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Link href="/deals" className={styles.allLink}>
        Все сделки
        <Icon name="chevron-right" size={14} />
      </Link>
    </div>
  );
}
