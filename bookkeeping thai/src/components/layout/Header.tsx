import { Icon } from '@/components/ui/Icon';
import { ThemeSwitcher } from '@/components/ui/ThemeSwitcher';
import styles from './Header.module.css';

interface HeaderProps {
  title: string;
  subtitle?: string;
  /** Отображаемый период сводки, например «01.05.2024 – 31.05.2024» */
  period: string;
  hasUnread?: boolean;
}

export function Header({ title, subtitle, period, hasUnread = false }: HeaderProps) {
  return (
    <header className={styles.header}>
      <div>
        <h1 className={styles.title}>{title}</h1>
        {subtitle ? <p className={styles.subtitle}>{subtitle}</p> : null}
      </div>

      <div className={styles.controls}>
        <button type="button" className={styles.period}>
          <Icon name="calendar" size={15} />
          <span>{period}</span>
          <Icon name="chevron-down" size={14} className={styles.periodChevron} />
        </button>

        <button type="button" className={styles.iconButton} aria-label="Уведомления">
          <Icon name="bell" size={17} />
          {hasUnread ? <span className={styles.unreadDot} aria-hidden="true" /> : null}
        </button>

        <ThemeSwitcher />
      </div>
    </header>
  );
}
