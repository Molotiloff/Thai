'use client';

import { Icon } from '@/components/ui/Icon';
import styles from './CitySelect.module.css';

interface CitySelectProps {
  cities: string[];
  /** Выбранный город или 'ALL' */
  value: string;
  onChange: (value: string) => void;
}

export function CitySelect({ cities, value, onChange }: CitySelectProps) {
  return (
    <label className={styles.wrap}>
      <select
        className={styles.select}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-label="Фильтр по городу"
      >
        <option value="ALL">Все города</option>
        {cities.map((city) => (
          <option key={city} value={city}>
            {city}
          </option>
        ))}
      </select>
      <Icon name="chevron-down" size={14} className={styles.chevron} />
    </label>
  );
}
