import type { CashDesk } from '@/types/domain';
import { formatMoney } from '@/lib/format';
import { Icon } from '@/components/ui/Icon';
import styles from './CashDesksGrid.module.css';

/** Кассы по городам и валютам */
export function CashDesksGrid({ desks }: { desks: CashDesk[] }) {
  return (
    <div className={styles.grid}>
      {desks.map((desk) => (
        <article key={desk.id} className={styles.desk}>
          <header className={styles.head}>
            <span className={styles.cityIcon}>
              <Icon name="building" size={15} />
            </span>
            <span className={styles.city}>{desk.city}</span>
          </header>
          <ul className={styles.balances}>
            {desk.balances.map((balance) => (
              <li key={balance.currency} className={styles.balanceRow}>
                <span className={styles.currency}>{balance.currency}</span>
                <span className={styles.amount}>{formatMoney(balance)}</span>
              </li>
            ))}
          </ul>
        </article>
      ))}
    </div>
  );
}
