'use client';

import type { OwnerTransaction } from '@/types/turnover';
import { formatRub } from '@/lib/format';
import { ChartCard } from '@/components/ui/ChartCard';
import { Icon } from '@/components/ui/Icon';
import tableStyles from './turnoverTables.module.css';
import styles from './OwnerTransactionsTable.module.css';

export function OwnerTransactionsTable({ rows }: { rows: OwnerTransaction[] }) {
  const handleAdd = () => {
    // TODO: открыть форму добавления вклада/вывода
    console.log('add owner transaction');
  };

  return (
    <ChartCard title="Вклады и выводы владельцев" subtitle="Движения капитала">
      <div className={tableStyles.scroll}>
        <table className={tableStyles.table}>
          <thead>
            <tr>
              <th>Дата</th>
              <th>Тип</th>
              <th>Владелец</th>
              <th className={tableStyles.num}>Сумма</th>
              <th>Комментарий</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.date}</td>
                <td>
                  <span
                    className={`${styles.kindBadge} ${
                      row.type === 'Вклад' ? styles.kindDeposit : styles.kindWithdrawal
                    }`}
                  >
                    {row.type}
                  </span>
                </td>
                <td className={tableStyles.strong}>{row.ownerName}</td>
                <td className={`${tableStyles.num} ${tableStyles.strong}`}>
                  {row.type === 'Вывод' ? '−' : '+'}
                  {formatRub(row.amount)}
                </td>
                <td>{row.comment ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <button type="button" className={styles.addRow} onClick={handleAdd}>
        <Icon name="plus" size={14} />
        Добавить
      </button>
    </ChartCard>
  );
}
