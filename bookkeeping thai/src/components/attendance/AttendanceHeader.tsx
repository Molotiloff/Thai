import { Icon } from '@/components/ui/Icon';
import { ThemeSwitcher } from '@/components/ui/ThemeSwitcher';
import { MonthSwitcher } from './MonthSwitcher';
import styles from './AttendanceHeader.module.css';

interface AttendanceHeaderProps {
  monthLabel: string;
}

export function AttendanceHeader({ monthLabel }: AttendanceHeaderProps) {
  return (
    <header className={styles.header}>
      <div>
        <h1 className={styles.title}>Посещаемость</h1>
        <p className={styles.subtitle}>Календарь посещаемости сотрудников</p>
      </div>

      <div className={styles.controls}>
        <MonthSwitcher monthLabel={monthLabel} />

        <button type="button" className={styles.iconButton} aria-label="Выбрать дату">
          <Icon name="calendar" size={16} />
        </button>

        {/* TODO: панель фильтров (роль, статус, сотрудник) */}
        <button type="button" className={styles.filters}>
          <Icon name="filter" size={15} />
          <span>Фильтры</span>
        </button>

        <ThemeSwitcher />
      </div>
    </header>
  );
}
