'use client';

import { useMemo, useState } from 'react';
import type { OwnerInvestmentShare } from '@/types/turnover';
import { formatRub } from '@/lib/format';
import { OWNER_COLORS } from './bits';
import styles from './OwnerInvestmentsChart.module.css';

const SIZE = 180;
const RADIUS = 74;
const STROKE = 20;
const GAP_DEG = 2.5;

function polar(angleDeg: number): { x: number; y: number } {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return {
    x: SIZE / 2 + RADIUS * Math.cos(rad),
    y: SIZE / 2 + RADIUS * Math.sin(rad),
  };
}

function arcPath(startDeg: number, endDeg: number): string {
  const start = polar(startDeg);
  const end = polar(endDeg);
  const largeArc = endDeg - startDeg > 180 ? 1 : 0;
  return `M${start.x.toFixed(2)},${start.y.toFixed(2)} A${RADIUS},${RADIUS} 0 ${largeArc} 1 ${end.x.toFixed(2)},${end.y.toFixed(2)}`;
}

/** Donut «Вложения по владельцам»: доли капитала, центр реагирует на ховер */
export function OwnerInvestmentsChart({ shares }: { shares: OwnerInvestmentShare[] }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const total = shares.reduce((sum, share) => sum + share.amount, 0);

  const arcs = useMemo(() => {
    const offsets = shares.map((_, i) => shares.slice(0, i).reduce((sum, s) => sum + s.percent, 0));
    return shares.map((share, i) => {
      const start = (offsets[i] / 100) * 360 + GAP_DEG / 2;
      const end = ((offsets[i] + share.percent) / 100) * 360 - GAP_DEG / 2;
      return { share, d: arcPath(start, Math.max(start + 1, end)) };
    });
  }, [shares]);

  const hovered = hoverIndex !== null ? shares[hoverIndex] : null;

  return (
    <div className={styles.wrapper}>
      <div className={styles.chartArea}>
        <svg
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          className={styles.svg}
          role="img"
          aria-label="Доли вложений по владельцам"
        >
          {arcs.map((arc, i) => (
            <path
              key={arc.share.ownerId}
              d={arc.d}
              fill="none"
              stroke={OWNER_COLORS[arc.share.ownerId]}
              strokeWidth={hoverIndex === i ? STROKE + 4 : STROKE}
              strokeLinecap="round"
              className={styles.segment}
              opacity={hoverIndex === null || hoverIndex === i ? 1 : 0.35}
              onMouseEnter={() => setHoverIndex(i)}
              onMouseLeave={() => setHoverIndex(null)}
            />
          ))}
        </svg>
        <div className={styles.center}>
          {hovered ? (
            <>
              <span className={styles.centerValue}>{formatRub(hovered.amount)}</span>
              <span className={styles.centerLabel}>{hovered.ownerName}</span>
            </>
          ) : (
            <>
              <span className={styles.centerValue}>{formatRub(total)}</span>
              <span className={styles.centerLabel}>Всего вложений</span>
            </>
          )}
        </div>
      </div>

      <ul className={styles.legend}>
        {shares.map((share, i) => (
          <li
            key={share.ownerId}
            className={styles.legendItem}
            onMouseEnter={() => setHoverIndex(i)}
            onMouseLeave={() => setHoverIndex(null)}
          >
            <span
              className={styles.legendDot}
              style={{ background: OWNER_COLORS[share.ownerId] }}
              aria-hidden="true"
            />
            <span className={styles.legendName}>{share.ownerName}</span>
            <span className={styles.legendPercent}>{share.percent}%</span>
            <span className={styles.legendAmount}>{formatRub(share.amount)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
