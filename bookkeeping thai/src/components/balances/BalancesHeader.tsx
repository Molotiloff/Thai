'use client';

import { ThemeSwitcher } from '@/components/ui/ThemeSwitcher';
import type { CurrencyFilterValue } from '@/types/balances';
import { SearchInput } from '@/components/ui/SearchInput';
import { CurrencyFilter } from './CurrencyFilter';
import { SortButton, type SortState } from './SortButton';
import styles from './BalancesHeader.module.css';

interface BalancesHeaderProps {
  currency: CurrencyFilterValue;
  currencies: string[];
  onCurrencyChange: (value: CurrencyFilterValue) => void;
  query: string;
  onQueryChange: (value: string) => void;
  sort: SortState;
  onSortToggle: () => void;
}

export function BalancesHeader({
  currency,
  currencies,
  onCurrencyChange,
  query,
  onQueryChange,
  sort,
  onSortToggle,
}: BalancesHeaderProps) {
  return (
    <header className={styles.header}>
      <div>
        <h1 className={styles.title}>Балансы</h1>
        <p className={styles.subtitle}>Клиенты с ненулевыми балансами</p>
      </div>

      <div className={styles.controls}>
        <CurrencyFilter
          value={currency}
          currencies={currencies}
          onChange={onCurrencyChange}
        />
        <SearchInput value={query} onChange={onQueryChange} placeholder="Поиск по клиентам" />
        <SortButton sort={sort} onToggle={onSortToggle} />
        <ThemeSwitcher />
      </div>
    </header>
  );
}
