'use client';

import { useMemo, useRef, useState } from 'react';
import type { ProfitPoint } from '@/types/domain';
import { formatRub } from '@/lib/format';
import styles from './ProfitLineChart.module.css';

interface ProfitLineChartProps {
  points: ProfitPoint[];
}

const W = 640;
const H = 230;
const PAD = { top: 12, right: 12, bottom: 26, left: 44 };

function niceCeil(value: number): number {
  const step = 500_000;
  return Math.ceil(value / step) * step;
}

function formatAxis(value: number): string {
  if (value === 0) return '0';
  return `${(value / 1_000_000).toLocaleString('ru-RU', { maximumFractionDigits: 1 })}M`;
}

/** Линейный график «Динамика прибыли»: одна серия, crosshair и tooltip по ховеру */
export function ProfitLineChart({ points }: ProfitLineChartProps) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const geometry = useMemo(() => {
    const maxY = niceCeil(Math.max(...points.map((p) => p.value)));
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const x = (i: number) => PAD.left + (i / (points.length - 1)) * innerW;
    const y = (v: number) => PAD.top + innerH - (v / maxY) * innerH;

    const coords = points.map((p, i) => ({ x: x(i), y: y(p.value) }));
    const linePath = coords
      .map((c, i) => `${i === 0 ? 'M' : 'L'}${c.x.toFixed(1)},${c.y.toFixed(1)}`)
      .join(' ');
    const areaPath = `${linePath} L${coords[coords.length - 1].x.toFixed(1)},${
      PAD.top + innerH
    } L${coords[0].x.toFixed(1)},${PAD.top + innerH} Z`;

    const tickCount = 4;
    const yTicks = Array.from({ length: tickCount + 1 }, (_, i) => (maxY / tickCount) * i);
    // Подписи X — каждая седьмая точка + последняя; соседей последней
    // пропускаем, чтобы подписи не наезжали друг на друга
    const lastIndex = points.length - 1;
    const xTickIndexes = points
      .map((_, i) => i)
      .filter((i) => i === lastIndex || (i % 7 === 0 && lastIndex - i >= 4));

    return { coords, linePath, areaPath, yTicks, xTickIndexes, y, innerH };
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
  const hoveredCoord = hoverIndex !== null ? geometry.coords[hoverIndex] : null;

  return (
    <div className={styles.wrapper}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className={styles.svg}
        role="img"
        aria-label="Динамика прибыли за период"
        onMouseMove={handleMove}
        onMouseLeave={() => setHoverIndex(null)}
      >
        <defs>
          <linearGradient id="profit-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-line-fill-from)" />
            <stop offset="100%" stopColor="var(--chart-line-fill-to)" />
          </linearGradient>
        </defs>

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
            x={geometry.coords[i].x}
            y={H - 8}
            className={styles.axisLabel}
            textAnchor={i === points.length - 1 ? 'end' : 'middle'}
          >
            {points[i].label}
          </text>
        ))}

        <path d={geometry.areaPath} fill="url(#profit-fill)" />
        <path d={geometry.linePath} className={styles.line} />

        {hoveredCoord ? (
          <g>
            <line
              x1={hoveredCoord.x}
              x2={hoveredCoord.x}
              y1={PAD.top}
              y2={PAD.top + geometry.innerH}
              className={styles.crosshair}
            />
            <circle cx={hoveredCoord.x} cy={hoveredCoord.y} r={4.5} className={styles.marker} />
          </g>
        ) : null}
      </svg>

      {hovered && hoveredCoord ? (
        <div
          className={styles.tooltip}
          style={{
            left: `${(hoveredCoord.x / W) * 100}%`,
            top: `${(hoveredCoord.y / H) * 100}%`,
          }}
        >
          <span className={styles.tooltipLabel}>{hovered.label}</span>
          <span className={styles.tooltipValue}>{formatRub(hovered.value)}</span>
        </div>
      ) : null}
    </div>
  );
}
