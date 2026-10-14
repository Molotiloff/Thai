'use client';

import { useMemo, useState } from 'react';
import type { TurnoverStructureSlice } from '@/types/turnover';
import { formatRub } from '@/lib/format';
import styles from './TurnoverStructureChart.module.css';

const SIZE = 180;
const RADIUS = 74;
const STROKE = 20;
const GAP_DEG = 2.5;

const SLICE_COLOR: Record<TurnoverStructureSlice['id'], string> = {
  purchase: 'var(--series-eth)',
  sale: 'var(--series-btc)',
};

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

interface TurnoverStructureChartProps {
  totalRub: number;
  slices: TurnoverStructureSlice[];
}

/** Donut «Структура оборота»: покупка/продажа, центр реагирует на ховер */
export function TurnoverStructureChart({ totalRub, slices }: TurnoverStructureChartProps) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const arcs = useMemo(() => {
    const offsets = slices.map((_, i) => slices.slice(0, i).reduce((sum, s) => sum + s.percent, 0));
    return slices.map((slice, i) => {
      const start = (offsets[i] / 100) * 360 + GAP_DEG / 2;
      const end = ((offsets[i] + slice.percent) / 100) * 360 - GAP_DEG / 2;
      return { slice, d: arcPath(start, Math.max(start + 1, end)) };
    });
  }, [slices]);

  const hovered = hoverIndex !== null ? slices[hoverIndex] : null;

  return (
    <div className={styles.wrapper}>
      <div className={styles.chartArea}>
        <svg
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          className={styles.svg}
          role="img"
          aria-label="Структура оборота: покупка и продажа"
        >
          {arcs.map((arc, i) => (
            <path
              key={arc.slice.id}
              d={arc.d}
              fill="none"
              stroke={SLICE_COLOR[arc.slice.id]}
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
              <span className={styles.centerValue}>{formatRub(hovered.amountRub)}</span>
              <span className={styles.centerLabel}>{hovered.label}</span>
            </>
          ) : (
            <>
              <span className={styles.centerValue}>{formatRub(totalRub)}</span>
              <span className={styles.centerLabel}>Всего</span>
            </>
          )}
        </div>
      </div>

      <ul className={styles.legend}>
        {slices.map((slice, i) => (
          <li
            key={slice.id}
            className={styles.legendItem}
            onMouseEnter={() => setHoverIndex(i)}
            onMouseLeave={() => setHoverIndex(null)}
          >
            <span
              className={styles.legendDot}
              style={{ background: SLICE_COLOR[slice.id] }}
              aria-hidden="true"
            />
            <span className={styles.legendLabel}>{slice.label}</span>
            <span className={styles.legendPercent}>{slice.percent}%</span>
            <span className={styles.legendAmount}>{formatRub(slice.amountRub)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
