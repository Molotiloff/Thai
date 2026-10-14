import Link from 'next/link';
import { Icon } from '@/components/ui/Icon';
import styles from './CurrenciesPanel.module.css';

export interface CashCurrencyCard {
  instrumentCode: string; label: string; amount: string; carryingUsdt: string;
  unvaluedAmount: string; hasUnvalued: boolean; href: string;
}
export function CurrenciesPanel({ currencies }: { currencies: CashCurrencyCard[] }) {
  return <section className={styles.card} aria-labelledby="cash-currencies-title">
    <div className={styles.header}><h2 id="cash-currencies-title" className={styles.heading}>Деньги в кассе</h2><span className={styles.caption}>8 счетов</span></div>
    <div className={styles.grid}>{currencies.map(currency => <article className={styles.currencyCard} key={currency.instrumentCode}>
      <div className={styles.currencyTop}><span className={styles.currencyLabel}>{currency.label}</span><Icon name={currency.instrumentCode === 'THB_BANK' ? 'building' : currency.instrumentCode === 'USDT' ? 'coins' : 'cash'} size={16}/></div>
      <span className={styles.currencyAmount}>{currency.amount}</span>
      <dl className={styles.currencyRows}>
        <div className={styles.currencyRow}><dt className={styles.currencyRowLabel}>Себестоимость USDT</dt><dd className={styles.currencyRowValue}>{currency.carryingUsdt}</dd></div>
        {currency.hasUnvalued && <div className={styles.currencyRow}><dt className={styles.currencyRowLabel}>Без оценки</dt><dd className={styles.currencyRowValue}>{currency.unvaluedAmount}</dd></div>}
      </dl>
      <Link className={styles.historyLink} href={currency.href}>Движения по счёту<Icon name="chevron-right" size={12}/></Link>
    </article>)}</div>
  </section>;
}
