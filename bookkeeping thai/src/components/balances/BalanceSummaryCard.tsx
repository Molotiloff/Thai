import type { CurrencySummary } from '@/types/balances';
import { formatCrypto, formatPercent } from '@/lib/format';
import { Icon } from '@/components/ui/Icon';
import { CurrencyBadge } from './CurrencyBadge';
import styles from './BalanceSummaryCard.module.css';

function formatSummaryValue({ code, value }: CurrencySummary): string {
  if (code === 'ALL') return formatCrypto(value, 'RUB');
  return formatCrypto(value, code);
}

export function BalanceSummaryCard({ summary }: { summary: CurrencySummary }) {
  return (
    <article className={styles.card}>
      <div className={styles.head}>
        {summary.code === 'ALL' ? (
          <span className={styles.allIcon}>
            <Icon name="coins" size={15} />
          </span>
        ) : (
          <CurrencyBadge currency={summary.code} size={26} />
        )}
        <span className={styles.label}>{summary.label}</span>
      </div>

      <span className={styles.value}>{formatSummaryValue(summary)}</span>

      {summary.changePercent !== undefined ? (
        <span
          className={`${styles.change} ${
            summary.tone === 'red' ? styles.changeDown : styles.changeUp
          }`}
        >
          {formatPercent(summary.changePercent)}
        </span>
      ) : null}
    </article>
  );
}
