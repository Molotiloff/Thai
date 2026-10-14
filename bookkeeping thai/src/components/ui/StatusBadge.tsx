import type { DealStatus } from '@/types/domain';
import styles from './StatusBadge.module.css';

/**
 * Конфигурация статусов (Open/Closed): новый статус добавляется
 * записью в map без изменения разметки компонента.
 */
const STATUS_CONFIG: Record<DealStatus, { label: string; className: string; alert?: boolean }> = {
  new: { label: 'Новая', className: styles.fixed },
  fixed: { label: 'Фикс с клиентом', className: styles.fixed },
  balance_check: { label: 'Сверка баланса', className: styles.check },
  awaiting_payment: { label: 'Ожидание оплаты', className: styles.waiting },
  in_delivery: { label: 'В доставке', className: styles.waiting },
  ready_for_cash_settlement: { label: 'Готова к выдаче', className: styles.check },
  done: { label: 'Сделка завершена', className: styles.done },
  canceled: { label: 'Отменена', className: styles.alert, alert: true },
};

export function StatusBadge({ status }: { status: DealStatus }) {
  const config = STATUS_CONFIG[status];
  return (
    <span className={`${styles.badge} ${config.className}`}>
      <span className={styles.dot} aria-hidden="true" />
      {config.label}
    </span>
  );
}
