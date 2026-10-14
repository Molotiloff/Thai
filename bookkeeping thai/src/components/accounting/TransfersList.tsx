import type { CashTransfer } from '@/types/domain';
import { formatMoney } from '@/lib/format';
import { Icon } from '@/components/ui/Icon';
import styles from './TransfersList.module.css';

/** Перемещения средств между кассами */
export function TransfersList({ transfers }: { transfers: CashTransfer[] }) {
  return (
    <ul className={styles.list}>
      {transfers.map((transfer) => (
        <li key={transfer.id} className={styles.item}>
          <span className={styles.icon}>
            <Icon name="deals" size={15} />
          </span>
          <span className={styles.body}>
            <span className={styles.route}>
              {transfer.fromCity}
              <Icon name="chevron-right" size={13} className={styles.routeArrow} />
              {transfer.toCity}
            </span>
            <span className={styles.meta}>
              {transfer.date}
              {transfer.comment ? ` · ${transfer.comment}` : ''}
            </span>
          </span>
          <span className={styles.amount}>{formatMoney(transfer.amount)}</span>
        </li>
      ))}
    </ul>
  );
}
