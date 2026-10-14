import type { ReactNode } from 'react';
import styles from './ChartCard.module.css';

interface ChartCardProps {
  title: string;
  subtitle?: string;
  /** Контрол в правом углу шапки (селект периода и т.п.) */
  action?: ReactNode;
  children: ReactNode;
}

/** Универсальная карточка секции: шапка + произвольное содержимое */
export function ChartCard({ title, subtitle, action, children }: ChartCardProps) {
  return (
    <section className={styles.card}>
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>{title}</h2>
          {subtitle ? <p className={styles.subtitle}>{subtitle}</p> : null}
        </div>
        {action ? <div className={styles.action}>{action}</div> : null}
      </header>
      <div className={styles.content}>{children}</div>
    </section>
  );
}
