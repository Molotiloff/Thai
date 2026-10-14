'use client';

import { Icon } from '@/components/ui/Icon';
import styles from './DashboardFooter.module.css';

export function DashboardFooter({ lastUpdatedLabel }: { lastUpdatedLabel: string }) {
  const handleRefresh = () => {
    // TODO: запросить свежие данные дашборда
    console.log('refresh dashboard');
  };

  return (
    <footer className={styles.footer}>
      <span className={styles.updated}>Последнее обновление: {lastUpdatedLabel}</span>
      <button
        type="button"
        className={styles.refreshButton}
        title="Обновить"
        aria-label="Обновить дашборд"
        onClick={handleRefresh}
      >
        <Icon name="refresh" size={15} />
      </button>
    </footer>
  );
}
