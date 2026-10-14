import { formatPercent } from '@/lib/format';
import styles from './bits.module.css';

/** Бейдж динамики: +12.5% зелёным / -3.1% красным */
export function PercentBadge({ value }: { value: number }) {
  return (
    <span className={`${styles.badge} ${value >= 0 ? styles.badgeUp : styles.badgeDown}`}>
      {formatPercent(value)}
    </span>
  );
}

interface ProgressBarProps {
  /** Заполнение, 0–100 */
  percent: number;
  color?: string;
}

export function ProgressBar({ percent, color }: ProgressBarProps) {
  return (
    <span className={styles.track} role="presentation">
      <span
        className={styles.fill}
        style={{ width: `${Math.min(100, Math.max(0, percent))}%`, background: color }}
      />
    </span>
  );
}

/** Цвета владельцев — фиксированный порядок, валидированные токены серий */
export const OWNER_COLORS: Record<string, string> = {
  'o-1': 'var(--series-usdt)',
  'o-2': 'var(--series-eth)',
  'o-3': 'var(--series-btc)',
  'o-4': 'var(--series-other)',
  'o-5': '#14b8a6',
  'o-6': '#a855f7',
  'o-7': '#64748b',
};
