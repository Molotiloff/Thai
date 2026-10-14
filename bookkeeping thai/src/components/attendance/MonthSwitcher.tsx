'use client';

import { Icon } from '@/components/ui/Icon';
import styles from './MonthSwitcher.module.css';

interface MonthSwitcherProps {
  monthLabel: string;
}

export function MonthSwitcher({ monthLabel }: MonthSwitcherProps) {
  // TODO: подключить реальное переключение месяца (загрузка данных за период)
  return (
    <div className={styles.switcher}>
      <button type="button" className={styles.arrow} aria-label="Предыдущий месяц">
        <Icon name="chevron-left" size={15} />
      </button>
      <span className={styles.label}>{monthLabel}</span>
      <button type="button" className={styles.arrow} aria-label="Следующий месяц">
        <Icon name="chevron-right" size={15} />
      </button>
    </div>
  );
}
