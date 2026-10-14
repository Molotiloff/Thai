'use client';

import { Icon } from '@/components/ui/Icon';
import type { CurrencyFilterValue } from '@/types/balances';
import styles from './CurrencyFilter.module.css';

interface CurrencyFilterProps {
  value: CurrencyFilterValue;
  currencies: string[];
  onChange: (value: CurrencyFilterValue) => void;
}

export function CurrencyFilter({ value, currencies, onChange }: CurrencyFilterProps) {
  return (
    <label className={styles.wrap}>
      <select
        className={styles.select}
        value={value}
        onChange={(event) => onChange(event.target.value as CurrencyFilterValue)}
        aria-label="Фильтр по валюте"
      >
        <option value="ALL">Все валюты</option>
        {currencies.map((currency) => (
          <option key={currency} value={currency}>
            {currency}
          </option>
        ))}
      </select>
      <Icon name="chevron-down" size={14} className={styles.chevron} />
    </label>
  );
}
