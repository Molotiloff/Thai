'use client';

import { Icon } from '@/components/ui/Icon';
import type { BalanceSortKey, SortDirection } from '@/types/balances';
import styles from './SortButton.module.css';

export interface SortState {
  key: BalanceSortKey;
  direction: SortDirection;
}

/** Порядок перебора сортировок по клику */
const SORT_CYCLE: SortState[] = [
  { key: 'balanceRub', direction: 'desc' },
  { key: 'balanceRub', direction: 'asc' },
  { key: 'name', direction: 'asc' },
  { key: 'name', direction: 'desc' },
];

const KEY_LABELS: Record<BalanceSortKey, string> = {
  name: 'по имени клиента',
  balance: 'по балансу',
  balanceRub: 'по сумме в RUB',
};

export function nextSortState(current: SortState): SortState {
  const index = SORT_CYCLE.findIndex(
    (state) => state.key === current.key && state.direction === current.direction,
  );
  return SORT_CYCLE[(index + 1) % SORT_CYCLE.length];
}

interface SortButtonProps {
  sort: SortState;
  onToggle: () => void;
}

export function SortButton({ sort, onToggle }: SortButtonProps) {
  const label = `Сортировка: ${KEY_LABELS[sort.key]} ${sort.direction === 'asc' ? '↑' : '↓'}`;
  return (
    <button
      type="button"
      className={styles.button}
      title={label}
      aria-label={label}
      onClick={onToggle}
    >
      <Icon name="sliders" size={16} />
    </button>
  );
}
