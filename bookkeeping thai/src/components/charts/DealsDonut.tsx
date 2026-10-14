'use client';

import { useMemo, useState } from 'react';
import type { DealDirection, DealStructure } from '@/types/domain';
import styles from './DealsDonut.module.css';

const SIZE = 180;
const RADIUS = 74;
const STROKE = 20;
/** Зазор между сегментами — обязательный разделитель по спецификации графиков */
const GAP_DEG = 2.5;

const SERIES_VAR: Record<DealDirection, string> = {
  'USDT/RUB': 'var(--series-usdt)',
  'BTC/RUB': 'var(--series-btc)',
  'ETH/RUB': 'var(--series-eth)',
  OTHER: 'var(--series-other)',
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

/** Donut «Структура сделок»: сегменты по направлениям, центр реагирует на ховер */
export function DealsDonut({ structure }: { structure: DealStructure }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const arcs = useMemo(() => {
    const offsets = structure.slices.map((_, i) =>
      structure.slices.slice(0, i).reduce((sum, s) => sum + s.percent, 0),
    );
    return structure.slices.map((slice, i) => {
      const start = (offsets[i] / 100) * 360 + GAP_DEG / 2;
      const end = ((offsets[i] + slice.percent) / 100) * 360 - GAP_DEG / 2;
      return { slice, d: arcPath(start, Math.max(start + 1, end)) };
    });
  }, [structure]);

  const hovered = hoverIndex !== null ? structure.slices[hoverIndex] : null;

  return (
    <div className={styles.wrapper}>
      <div className={styles.chartArea}>
        <svg
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          className={styles.svg}
          role="img"
          aria-label="Структура сделок по валютным направлениям"
        >
          {arcs.map((arc, i) => (
            <path
              key={arc.slice.direction}
              d={arc.d}
              fill="none"
              stroke={SERIES_VAR[arc.slice.direction]}
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
              <span className={styles.centerValue}>{hovered.percent}%</span>
              <span className={styles.centerLabel}>{hovered.label}</span>
            </>
          ) : (
            <>
              <span className={styles.centerValue}>
                {structure.totalDeals.toLocaleString('ru-RU')}
              </span>
              <span className={styles.centerLabel}>Всего сделок</span>
            </>
          )}
        </div>
      </div>

      <ul className={styles.legend}>
        {structure.slices.map((slice, i) => (
          <li
            key={slice.direction}
            className={styles.legendItem}
            onMouseEnter={() => setHoverIndex(i)}
            onMouseLeave={() => setHoverIndex(null)}
          >
            <span
              className={styles.legendDot}
              style={{ background: SERIES_VAR[slice.direction] }}
              aria-hidden="true"
            />
            <span className={styles.legendLabel}>{slice.label}</span>
            <span className={styles.legendValue}>{slice.percent}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
