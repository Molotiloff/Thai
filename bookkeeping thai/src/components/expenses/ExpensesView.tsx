'use client';

import { useMemo, useState } from 'react';
import type { ExpenseCityFilter, ExpensesPageData, ExpenseTab } from '@/types/expenses';
import { ExpensesHeader } from './ExpensesHeader';
import { ExpensesTabs } from './ExpensesTabs';
import { ExpenseMetricGrid } from './ExpenseMetricGrid';
import { FixedExpensesTable } from './FixedExpensesTable';
import { VariableExpensesTable } from './VariableExpensesTable';
import { ExpensePagination } from './ExpensePagination';
import styles from './ExpensesView.module.css';

/** Фильтр города применяется к обеим таблицам локально */
export function ExpensesView({ data }: { data: ExpensesPageData }) {
  const [tab, setTab] = useState<ExpenseTab>('all');
  const [city, setCity] = useState<ExpenseCityFilter>('Все города');

  const fixedExpenses = useMemo(
    () =>
      city === 'Все города'
        ? data.fixedExpenses
        : data.fixedExpenses.filter((expense) => expense.city === city),
    [data.fixedExpenses, city],
  );

  const variableExpenses = useMemo(
    () =>
      city === 'Все города'
        ? data.variableExpenses
        : data.variableExpenses.filter((expense) => expense.city === city),
    [data.variableExpenses, city],
  );

  return (
    <>
      <ExpensesHeader
        periodLabel={data.periodLabel}
        cities={data.cities}
        city={city}
        onCityChange={setCity}
      />

      <ExpensesTabs active={tab} onChange={setTab} />

      {tab === 'all' ? (
        <>
          <ExpenseMetricGrid metrics={data.metrics} />
          <FixedExpensesTable expenses={fixedExpenses} moreCount={data.moreFixedCount} />
          <VariableExpensesTable expenses={variableExpenses} moreCount={data.moreVariableCount} />
          <ExpensePagination totalCount={data.totalCount} />
        </>
      ) : (
        <div className={styles.reportsPlaceholder}>
          Отчёты по расходам появятся здесь. Раздел в разработке.
        </div>
      )}
    </>
  );
}
