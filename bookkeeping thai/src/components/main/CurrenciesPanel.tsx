import type { CurrencyFactCard as CurrencyFactCardData } from '@/types/mainDashboard';
import { formatNumber, formatRub } from '@/lib/format';
import { TONE_TEXT_CLASS } from './toneStyles';
import styles from './CurrenciesPanel.module.css';

function CurrencyFactCard({ currency }: { currency: CurrencyFactCardData }) {
  const rows = [
    { label: 'Курс', value: currency.rate.toLocaleString('ru-RU', { maximumFractionDigits: 3 }) },
    { label: `${currency.label} в рубле`, value: formatRub(currency.rubValue) },
    { label: `${currency.label} клиент.`, value: formatNumber(currency.clientAmount) },
    ...(currency.code === 'USDT'
      ? [{ label: 'Прибыль со сделок', value: formatNumber(currency.dealProfitAmount) }]
      : []),
    { label: `${currency.label} факт`, value: formatNumber(currency.factAmount) },
    ...(currency.observedAmount === undefined
      ? []
      : [{ label: 'Наблюдаемый остаток', value: formatNumber(currency.observedAmount) }]),
    ...(currency.gap === undefined
      ? []
      : [{ label: 'Разрыв', value: formatNumber(currency.gap) }]),
  ];

  return (
    <article className={styles.currencyCard}>
      <span className={`${styles.currencyLabel} ${TONE_TEXT_CLASS[currency.tone]}`}>
        {currency.label}
      </span>

      <span className={styles.currencyAmount}>{formatNumber(currency.amount)}</span>

      <dl className={styles.currencyRows}>
        {rows.map((row) => (
          <div key={row.label} className={styles.currencyRow}>
            <dt className={styles.currencyRowLabel}>{row.label}</dt>
            <dd className={styles.currencyRowValue}>{row.value}</dd>
          </div>
        ))}
      </dl>
    </article>
  );
}

export function CurrenciesPanel({ currencies }: { currencies: CurrencyFactCardData[] }) {
  return (
    <section className={styles.card}>
      <h2 className={styles.heading}>Валюты</h2>
      <div className={styles.grid}>
        {currencies.map((currency) => (
          <CurrencyFactCard key={currency.code} currency={currency} />
        ))}
      </div>
    </section>
  );
}
