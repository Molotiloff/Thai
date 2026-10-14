'use client';

import { useMemo, useState } from 'react';
import type { BalancesSnapshot, CurrencyFilterValue } from '@/types/balances';
import { BalancesHeader } from './BalancesHeader';
import { BalanceSummaryCard } from './BalanceSummaryCard';
import { BalancesTable } from './BalancesTable';
import { InfoAlert } from '@/components/ui/InfoAlert';
import { nextSortState, type SortState } from './SortButton';
import styles from './BalancesView.module.css';

const SUMMARY_CURRENCIES = new Set(['USD', 'USDT', 'USDW', 'EUR', 'RUB', 'ALL']);

/** Фильтр по валюте, поиск и сортировка работают на клиенте поверх снапшота */
export function BalancesView({ snapshot }: { snapshot: BalancesSnapshot }) {
  const [currency, setCurrency] = useState<CurrencyFilterValue>('ALL');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<SortState>({ key: 'balanceRub', direction: 'desc' });
  const visibleSummaries = snapshot.summaries.filter((summary) =>
    SUMMARY_CURRENCIES.has(summary.code),
  );

  const currencies = useMemo(
    () => [...new Set(snapshot.clients.map((client) => client.currency))].sort(),
    [snapshot.clients],
  );

  const visibleClients = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = snapshot.clients.filter((client) => {
      if (currency !== 'ALL' && client.currency !== currency) return false;
      if (!q) return true;
      return (
        client.name.toLowerCase().includes(q) || client.clientNumber.toLowerCase().includes(q)
      );
    });

    const direction = sort.direction === 'asc' ? 1 : -1;
    return [...filtered].sort((a, b) => {
      if (sort.key === 'name') return a.name.localeCompare(b.name, 'ru') * direction;
      return (a[sort.key] - b[sort.key]) * direction;
    });
  }, [snapshot.clients, currency, query, sort]);

  return (
    <>
      <BalancesHeader
        currency={currency}
        currencies={currencies}
        onCurrencyChange={setCurrency}
        query={query}
        onQueryChange={setQuery}
        sort={sort}
        onSortToggle={() => setSort(nextSortState)}
      />

      <div className={styles.summaryGrid}>
        {visibleSummaries.map((summary) => (
          <BalanceSummaryCard key={summary.code} summary={summary} />
        ))}
      </div>

      <BalancesTable clients={visibleClients} />

      <div className={styles.alertWrap}>
        <InfoAlert>Здесь отображаются только клиенты с ненулевыми балансами.</InfoAlert>
      </div>
    </>
  );
}
