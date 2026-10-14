'use client';

import type { VariableExpense } from '@/types/expenses';
import { formatRub } from '@/lib/format';
import { Icon } from '@/components/ui/Icon';
import { ExpenseCityBadge } from './ExpenseBadges';
import styles from './expensesTables.module.css';

interface VariableExpensesTableProps {
  expenses: VariableExpense[];
  moreCount: number;
}

export function VariableExpensesTable({ expenses, moreCount }: VariableExpensesTableProps) {
  const handleRowClick = (expense: VariableExpense) => {
    // TODO: открыть карточку/редактирование расхода
    console.log('variable expense', expense.id);
  };

  return (
    <section className={styles.card}>
      <h2 className={styles.heading}>Переменные расходы</h2>

      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.firstHead}>Расход</th>
              <th>Сумма</th>
              <th>Дата</th>
              <th>Город</th>
              <th className={styles.lastHead} aria-label="Действия" />
            </tr>
          </thead>
          <tbody>
            {expenses.map((expense) => (
              <tr key={expense.id} className={styles.row} onClick={() => handleRowClick(expense)}>
                <td className={`${styles.firstCol} ${styles.amountName}`}>{expense.name}</td>
                <td className={styles.amount}>{formatRub(expense.amount)}</td>
                <td>{expense.date}</td>
                <td>
                  <ExpenseCityBadge city={expense.city} />
                </td>
                <td className={`${styles.lastCol} ${styles.chevronCol}`}>
                  <Icon name="chevron-down" size={14} />
                </td>
              </tr>
            ))}
            {expenses.length === 0 ? (
              <tr>
                <td colSpan={5} className={styles.emptyCell}>
                  Нет расходов по выбранному городу.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <button
        type="button"
        className={styles.showMore}
        onClick={() => console.log('show more variable expenses')}
      >
        Показать ещё {moreCount}
      </button>
    </section>
  );
}
