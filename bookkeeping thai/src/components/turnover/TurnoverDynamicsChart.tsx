'use client';

import { useMemo, useRef, useState } from 'react';
import type { TurnoverDailyPoint } from '@/types/turnover';
import { formatRub } from '@/lib/format';
import styles from './TurnoverDynamicsChart.module.css';

const W = 640;
const H = 250;
const PAD = { top: 14, right: 12, bottom: 26, left: 38 };

/** Фиксированный порядок серий; цвета — валидированные токены графиков */
const SERIES = [
  { key: 'turnover', label: 'Оборот', color: 'var(--series-usdt)' },
  { key: 'purchase', label: 'Покупка', color: 'var(--series-eth)' },
  { key: 'sale', label: 'Продажа', color: 'var(--series-btc)' },
] as const;

function niceCeil(value: number): number {
  const step = 500_000;
  return Math.ceil(value / step) * step;
}

function formatAxis(value: number): string {
  return value === 0
    ? '0'
    : (value / 1_000_000).toLocaleString('ru-RU', { maximumFractionDigits: 1 });
}

/** Три линии динамики оборота с общим crosshair и tooltip */
export function TurnoverDynamicsChart({ points }: { points: TurnoverDailyPoint[] }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const geometry = useMemo(() => {
    const maxY = niceCeil(Math.max(...points.map((p) => p.turnover)));
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const x = (i: number) => PAD.left + (i / (points.length - 1)) * innerW;
    const y = (v: number) => PAD.top + innerH - (v / maxY) * innerH;

    const paths = SERIES.map((series) =>
      points
        .map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p[series.key]).toFixed(1)}`)
        .join(' '),
    );

    const tickCount = 4;
    const yTicks = Array.from({ length: tickCount + 1 }, (_, i) => (maxY / tickCount) * i);
    const lastIndex = points.length - 1;
    const xTickIndexes = points
      .map((_, i) => i)
      .filter((i) => i === lastIndex || (i % 7 === 0 && lastIndex - i >= 4));

    return { x, y, paths, yTicks, xTickIndexes, innerH };
  }, [points]);

  function handleMove(event: React.MouseEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * W;
    const innerW = W - PAD.left - PAD.right;
    const ratio = Math.min(1, Math.max(0, (px - PAD.left) / innerW));
    setHoverIndex(Math.round(ratio * (points.length - 1)));
  }

  const hovered = hoverIndex !== null ? points[hoverIndex] : null;
  const hoverX = hoverIndex !== null ? geometry.x(hoverIndex) : null;

  return (
    <div className={styles.wrapper}>
      <ul className={styles.legend}>
        {SERIES.map((series) => (
          <li key={series.key} className={styles.legendItem}>
            <span className={styles.legendDot} style={{ background: series.color }} aria-hidden="true" />
            {series.label}
          </li>
        ))}
      </ul>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className={styles.svg}
        role="img"
        aria-label="Динамика оборота, покупки и продажи за период"
        onMouseMove={handleMove}
        onMouseLeave={() => setHoverIndex(null)}
      >
        <text x={PAD.left - 6} y={9} className={styles.axisUnit} textAnchor="start">
          млн ₽
        </text>

        {geometry.yTicks.map((tick) => (
          <g key={tick}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={geometry.y(tick)}
              y2={geometry.y(tick)}
              className={styles.grid}
            />
            <text x={PAD.left - 8} y={geometry.y(tick) + 3.5} className={styles.axisLabel} textAnchor="end">
              {formatAxis(tick)}
            </text>
          </g>
        ))}

        {geometry.xTickIndexes.map((i) => (
          <text
            key={i}
            x={geometry.x(i)}
            y={H - 8}
            className={styles.axisLabel}
            textAnchor={i === points.length - 1 ? 'end' : 'middle'}
          >
            {points[i].label}
          </text>
        ))}

        {geometry.paths.map((d, i) => (
          <path key={SERIES[i].key} d={d} className={styles.line} stroke={SERIES[i].color} />
        ))}

        {hoverX !== null && hovered ? (
          <g>
            <line
              x1={hoverX}
              x2={hoverX}
              y1={PAD.top}
              y2={PAD.top + geometry.innerH}
              className={styles.crosshair}
            />
            {SERIES.map((series) => (
              <circle
                key={series.key}
                cx={hoverX}
                cy={geometry.y(hovered[series.key])}
                r={4}
                fill={series.color}
                className={styles.marker}
              />
            ))}
          </g>
        ) : null}
      </svg>

      {hovered && hoverX !== null ? (
        <div
          className={styles.tooltip}
          style={{
            left: `${(hoverX / W) * 100}%`,
            top: `${(geometry.y(hovered.turnover) / H) * 100}%`,
          }}
        >
          <span className={styles.tooltipLabel}>{hovered.label} 2024</span>
          {SERIES.map((series) => (
            <span key={series.key} className={styles.tooltipRow}>
              <span className={styles.legendDot} style={{ background: series.color }} aria-hidden="true" />
              <span className={styles.tooltipSeries}>{series.label}</span>
              <span className={styles.tooltipValue}>{formatRub(hovered[series.key])}</span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
