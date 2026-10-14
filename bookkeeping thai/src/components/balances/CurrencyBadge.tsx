import type { CurrencyCode } from '@/types/balances';
import styles from './CurrencyBadge.module.css';

const CURRENCY_VISUALS: Readonly<Record<string, { symbol: string; className: string }>> = {
  USDT: { symbol: '₮', className: styles.usdt },
  RUB: { symbol: '₽', className: styles.rub },
  USD: { symbol: '$', className: styles.usd },
  USD_BL: { symbol: '$', className: styles.usd },
  USD_WH: { symbol: '$', className: styles.usd },
  EUR: { symbol: '€', className: styles.eur },
  BTC: { symbol: '₿', className: styles.btc },
  ETH: { symbol: 'Ξ', className: styles.eth },
  CNY: { symbol: '¥', className: styles.cny },
};

interface CurrencyBadgeProps {
  currency: CurrencyCode;
  size?: number;
}

export function CurrencyBadge({ currency, size = 26 }: CurrencyBadgeProps) {
  const { symbol, className } = CURRENCY_VISUALS[currency] ?? {
    symbol: currency.slice(0, 1).toUpperCase() || '?',
    className: styles.other,
  };
  return (
    <span
      className={`${styles.badge} ${className}`}
      style={{ width: size, height: size, fontSize: size * 0.5 }}
      aria-hidden="true"
    >
      {symbol}
    </span>
  );
}
