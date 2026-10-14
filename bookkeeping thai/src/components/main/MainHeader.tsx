import { Icon } from '@/components/ui/Icon';
import { ThemeSwitcher } from '@/components/ui/ThemeSwitcher';
import styles from './MainHeader.module.css';

interface MainHeaderProps {
  dateLabel: string;
  weekdayLabel: string;
}

export function MainHeader({ dateLabel, weekdayLabel }: MainHeaderProps) {
  return (
    <header className={styles.header}>
      <div>
        <h1 className={styles.title}>Главная</h1>
        <p className={styles.subtitle}>Финансовые показатели</p>
      </div>

      <div className={styles.controls}>
        <span className={styles.date}>
          <Icon name="calendar" size={15} />
          <span className={styles.dateText}>
            <span className={styles.dateValue}>{dateLabel}</span>
            <span className={styles.weekday}>{weekdayLabel}</span>
          </span>
        </span>

        <ThemeSwitcher />
      </div>
    </header>
  );
}
