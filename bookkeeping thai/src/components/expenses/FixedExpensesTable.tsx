'use client';

import type { FixedExpense } from '@/types/expenses';
import { formatRub } from '@/lib/format';
import { ExpenseCategoryBadge, ExpenseCityBadge } from './ExpenseBadges';
import styles from './expensesTables.module.css';

interface FixedExpensesTableProps {
  expenses: FixedExpense[];
  moreCount: number;
}

export function FixedExpensesTable({ expenses, moreCount }: FixedExpensesTableProps) {
  const handleRowClick = (expense: FixedExpense) => {
    // TODO: открыть карточку/редактирование расхода
    console.log('fixed expense', expense.id);
  };

  return (
    <section className={styles.card}>
      <h2 className={styles.heading}>Постоянные расходы</h2>

      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.firstHead}>Категория</th>
              <th>Сумма</th>
              <th>Дата</th>
              <th>Комментарий</th>
              <th className={styles.lastHead}>Город</th>
            </tr>
          </thead>
          <tbody>
            {expenses.map((expense) => (
              <tr key={expense.id} className={styles.row} onClick={() => handleRowClick(expense)}>
                <td className={styles.firstCol}>
                  <ExpenseCategoryBadge category={expense.category} />
                </td>
                <td className={styles.amount}>{formatRub(expense.amount)}</td>
                <td>{expense.date}</td>
                <td>{expense.comment}</td>
                <td className={styles.lastCol}>
                  <ExpenseCityBadge city={expense.city} />
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
        onClick={() => console.log('show more fixed expenses')}
      >
        Показать ещё {moreCount}
      </button>
    </section>
  );
}
