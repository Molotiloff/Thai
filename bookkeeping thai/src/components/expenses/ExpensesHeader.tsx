'use client';

import { Icon } from '@/components/ui/Icon';
import { ThemeSwitcher } from '@/components/ui/ThemeSwitcher';
import type { ExpenseCity, ExpenseCityFilter } from '@/types/expenses';
import styles from './ExpensesHeader.module.css';

interface ExpensesHeaderProps {
  periodLabel: string;
  cities: ExpenseCity[];
  city: ExpenseCityFilter;
  onCityChange: (value: ExpenseCityFilter) => void;
}

export function ExpensesHeader({ periodLabel, cities, city, onCityChange }: ExpensesHeaderProps) {
  return (
    <header className={styles.header}>
      <div>
        <h1 className={styles.title}>Расходы</h1>
        <p className={styles.subtitle}>Аналитика расходов компании</p>
      </div>

      <div className={styles.controls}>
        {/* TODO: реальный выбор периода */}
        <button type="button" className={styles.period}>
          <span>{periodLabel}</span>
          <Icon name="calendar" size={15} />
        </button>

        <span className={styles.selectWrap}>
          <select
            className={styles.select}
            value={city}
            onChange={(event) => onCityChange(event.target.value as ExpenseCityFilter)}
            aria-label="Фильтр по городу"
          >
            <option value="Все города">Все города</option>
            {cities.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <Icon name="chevron-down" size={14} className={styles.selectChevron} />
        </span>

        {/* TODO: панель фильтров расходов */}
        <button
          type="button"
          className={styles.filters}
          onClick={() => console.log('open expense filters')}
        >
          <Icon name="filter" size={14} />
          <span>Фильтры</span>
        </button>

        <button
          type="button"
          className={styles.createButton}
          onClick={() => {
            // TODO: modal/drawer создания расхода (тип, поля, сохранение через API)
            console.log('create expense');
          }}
        >
          <Icon name="plus" size={15} />
          <span>Новый расход</span>
        </button>

        <ThemeSwitcher />
      </div>
    </header>
  );
}
