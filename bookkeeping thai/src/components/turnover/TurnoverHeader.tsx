'use client';

import { useState } from 'react';
import { Icon } from '@/components/ui/Icon';
import { ThemeSwitcher } from '@/components/ui/ThemeSwitcher';
import { CitySelect } from '@/components/deals/CitySelect';
import styles from './TurnoverHeader.module.css';

interface TurnoverHeaderProps {
  periodLabel: string;
  cities: string[];
}

export function TurnoverHeader({ periodLabel, cities }: TurnoverHeaderProps) {
  // Селект города пока placeholder — данные не фильтрует
  const [city, setCity] = useState('ALL');

  return (
    <header className={styles.header}>
      <div>
        <h1 className={styles.title}>Оборот</h1>
        <p className={styles.subtitle}>Аналитика оборота компании</p>
      </div>

      <div className={styles.controls}>
        {/* TODO: реальный выбор периода */}
        <button type="button" className={styles.period} onClick={() => console.log('change period')}>
          <span>{periodLabel}</span>
          <Icon name="calendar" size={15} />
        </button>

        <CitySelect cities={cities} value={city} onChange={setCity} />

        {/* TODO: панель фильтров */}
        <button type="button" className={styles.filters} onClick={() => console.log('open filters')}>
          <Icon name="filter" size={14} />
          <span>Фильтры</span>
        </button>

        <ThemeSwitcher />
      </div>
    </header>
  );
}
