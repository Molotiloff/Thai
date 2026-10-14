'use client';

import type { ExpenseTab } from '@/types/expenses';
import styles from './ExpensesTabs.module.css';

interface ExpensesTabsProps {
  active: ExpenseTab;
  onChange: (tab: ExpenseTab) => void;
}

export function ExpensesTabs({ active, onChange }: ExpensesTabsProps) {
  const handleReports = () => {
    // TODO: реализовать вкладку отчётов по расходам
    console.log('open expense reports');
    onChange('reports');
  };

  return (
    <div className={styles.tabs} role="tablist" aria-label="Разделы расходов">
      <button
        type="button"
        role="tab"
        aria-selected={active === 'all'}
        className={`${styles.tab} ${active === 'all' ? styles.tabActive : ''}`}
        onClick={() => onChange('all')}
      >
        Все расходы
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={active === 'reports'}
        className={`${styles.tab} ${active === 'reports' ? styles.tabActive : ''}`}
        onClick={handleReports}
      >
        Отчёты
      </button>
    </div>
  );
}
