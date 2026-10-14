'use client';

import { Icon } from '@/components/ui/Icon';
import { ThemeSwitcher } from '@/components/ui/ThemeSwitcher';
import { SearchInput } from '@/components/ui/SearchInput';
import styles from './ClientsHeader.module.css';

interface ClientsHeaderProps {
  query: string;
  onQueryChange: (value: string) => void;
  onCreate: () => void;
  onToggleFilters: () => void;
  canCreate: boolean;
}

export function ClientsHeader({ query, onQueryChange, onCreate, onToggleFilters, canCreate }: ClientsHeaderProps) {
  return (
    <header className={styles.header}>
      <div>
        <h1 className={styles.title}>Клиенты</h1>
        <p className={styles.subtitle}>Управление клиентской базой</p>
      </div>

      <div className={styles.controls}>
        <SearchInput value={query} onChange={onQueryChange} placeholder="Поиск по клиентам" />

        <button type="button" className={styles.filters} onClick={onToggleFilters}>
          <Icon name="filter" size={14} />
          <span>Фильтры</span>
        </button>

        {canCreate ? <button type="button" className={styles.createButton} onClick={onCreate}>
          <Icon name="plus" size={15} />
          <span>Новый клиент</span>
        </button> : null}

        <ThemeSwitcher />
      </div>
    </header>
  );
}
