import type { IconName } from '@/components/ui/Icon';
import type { DealStatus } from '@/types/domain';

/**
 * Визуальная мета статусов сделки: подпись, иконка и модификатор
 * для CSS-классов тонировки (fixed/waiting/check/done/alert).
 */
export const DEAL_STATUS_META: Record<
  DealStatus,
  { label: string; icon: IconName; modifier: 'fixed' | 'waiting' | 'check' | 'done' | 'alert' }
> = {
  new: { label: 'Новая', icon: 'plus', modifier: 'fixed' },
  fixed: { label: 'Фикс с клиентом', icon: 'deals', modifier: 'fixed' },
  balance_check: { label: 'Сверка баланса', icon: 'scale', modifier: 'check' },
  awaiting_payment: { label: 'Ожидание оплаты', icon: 'clock', modifier: 'waiting' },
  in_delivery: { label: 'В доставке', icon: 'plane', modifier: 'waiting' },
  ready_for_cash_settlement: {
    label: 'Готова к выдаче',
    icon: 'check-circle',
    modifier: 'check',
  },
  done: { label: 'Сделка завершена', icon: 'check-circle', modifier: 'done' },
  canceled: { label: 'Отменена', icon: 'x-circle', modifier: 'alert' },
};

/** Порядок статусов в сводке и на kanban-доске */
export const DEAL_STATUS_ORDER: DealStatus[] = [
  'new',
  'fixed',
  'balance_check',
  'awaiting_payment',
  'in_delivery',
  'ready_for_cash_settlement',
  'done',
  'canceled',
];
