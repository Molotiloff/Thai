import Link from 'next/link';
import type { ReactNode } from 'react';
import { Icon } from '@/components/ui/Icon';
import { ThemeSwitcher } from '@/components/ui/ThemeSwitcher';
import { InfoAlert } from '@/components/ui/InfoAlert';
import { TopMetricsGrid } from '@/components/main/TopMetricsGrid';
import type { TopMetric } from '@/types/mainDashboard';
import headerStyles from '@/components/deals/DealsHeader.module.css';
import { labels, money, monthNow, text, type Row } from './types';
import styles from './thai.module.css';

export type Params = Record<string, string | string[] | undefined>;
export function param(params: Params, key: string, fallback = ''): string {
  const value = params[key];
  return typeof value === 'string' ? value : fallback;
}
export function period(params: Params): string {
  const value = param(params, 'period', monthNow()).slice(0, 7);
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(value) ? value + '-01' : monthNow();
}

export function Page({ title, subtitle, children, actions, back }: {
  title: string; subtitle: string; children: ReactNode; actions?: ReactNode;
  back?: { href: string; label: string };
}) {
  return <div className={styles.page}>
    {back && <Link className={styles.back} href={back.href}><Icon name="arrow-left" size={15}/>{back.label}</Link>}
    <header className={`${headerStyles.header} ${styles.pageHeader}`}>
      <div><h1 className={headerStyles.title}>{title}</h1><p className={headerStyles.subtitle}>{subtitle}</p></div>
      <div className={styles.headerActions}>{actions}<ThemeSwitcher/></div>
    </header>
    {children}
  </div>;
}

export function Panel({ title, children, action, description }: {
  title: string; children: ReactNode; action?: ReactNode; description?: string;
}) {
  return <section className={styles.panel}>
    <header className={styles.panelHeader}>
      <div><h2>{title}</h2>{description && <p className={styles.panelDescription}>{description}</p>}</div>
      {action}
    </header>
    {children}
  </section>;
}

export function Metrics({ values }: { values: [string, unknown, string?][] }) {
  const metrics: TopMetric[] = values.map(([title, value, unit], index) => ({
    id: title, title, value: `${money(value)}${unit ? ' ' + unit : ''}`,
    icon: /процент|%/i.test(title) ? 'percent' : /прибыль|доход|доля/i.test(title) ? 'trend-up' : /сделок/i.test(title) ? 'deals' : 'wallet',
    tone: /^-\d/.test(String(value)) ? 'red' : /прибыль|доход/i.test(title) ? 'green' : /расход|процент|комисси/i.test(title) ? 'orange' : index % 2 ? 'purple' : 'blue',
  }));
  return <div className={styles.metrics}><TopMetricsGrid metrics={metrics}/></div>;
}

export function PeriodFilter({ value, extra }: { value: string; extra?: ReactNode }) {
  return <form className={styles.filters}>
    <label className={styles.periodLabel}><span><Icon name="calendar" size={14}/>Период</span><input name="period" type="month" defaultValue={value.slice(0, 7)} required/></label>
    {extra}
    <button type="submit"><Icon name="filter" size={14}/>Показать</button>
  </form>;
}

export interface Column { key: string; title: string; href?: string; amount?: boolean }
function cellContent(value: unknown, column: Column): ReactNode {
  if (column.amount) return <span className={/^-\d/.test(String(value)) ? styles.negative : undefined}>{money(value)}</span>;
  const raw = text(value);
  if (['active', 'is_active', 'cancelled'].includes(column.key) && typeof value === 'boolean') {
    const positive = column.key === 'cancelled' ? !value : value;
    const label = column.key === 'cancelled' ? (value ? 'Отменён' : 'Нет') : (value ? 'Действует' : 'Не действует');
    return <span className={`${styles.status} ${positive ? styles.statusDone : styles.statusMuted}`}>{label}</span>;
  }
  if (['status', 'effective_status', 'confirmed'].includes(column.key) && value != null) {
    const done = ['posted', 'Подтверждено', 'Проведено'].includes(raw);
    const tone = done ? styles.statusDone : raw === 'cancelled' ? styles.statusAlert : styles.statusWaiting;
    return <span className={`${styles.status} ${tone}`}>{labels[raw] ?? raw}</span>;
  }
  if (/^(business_date|period|valid_from|valid_until)$/.test(column.key) && /^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    return <time dateTime={raw}>{raw.split('-').reverse().join('.')}</time>;
  }
  if (/^(instrument_code|currency_code|payout_instrument_code|cash_instrument_code|instrument)$/.test(column.key) && value != null) {
    return <span className={styles.currency}>{labels[raw] ?? raw}</span>;
  }
  return labels[raw] ?? (typeof value === 'boolean' ? (value ? 'Да' : 'Нет') : raw);
}

export function Table({ data, columns, empty = 'За выбранный период записей нет' }: {
  data: Row[]; columns: Column[]; empty?: string;
}) {
  if (!data.length) return <div className={styles.empty}><Icon name="file-text" size={24}/><p>{empty}</p></div>;
  return <div className={styles.scroll} tabIndex={0} role="region" aria-label="Таблица данных, доступна горизонтальная прокрутка">
    <table><thead><tr>{columns.map(column => <th scope="col" className={column.amount ? styles.amount : undefined} key={column.key}>{column.title}</th>)}</tr></thead>
      <tbody>{data.map((row, index) => <tr key={`${String(row.id ?? row.operation_id ?? row.instrument_code ?? index)}:${index}`}>
        {columns.map(column => {
          const value = row[column.key];
          const href = column.href?.replace(/\{(\w+)\}/g, (_, key) => encodeURIComponent(text(row[key])));
          const wrap = /^(name|comment|reason|note|party_name|client_name)$/.test(column.key);
          return <td className={column.amount ? styles.amount : wrap ? styles.wrapCell : undefined} key={column.key}>
            {href && value != null ? <Link className={styles.link} href={href}>{cellContent(value, column)}<Icon name="chevron-right" size={13}/></Link> : cellContent(value, column)}
          </td>;
        })}
      </tr>)}</tbody>
    </table>
  </div>;
}

export function More({ href, cursor, name }: { href: string; cursor: unknown; name: string }) {
  if (!cursor) return null;
  return <div className={styles.pagination}><Link className={styles.secondaryButton} href={`${href}${href.includes('?') ? '&' : '?'}${name}=${encodeURIComponent(String(cursor))}`}>Следующая страница<Icon name="chevron-right" size={14}/></Link></div>;
}
export const opColumn: Column = { key: 'operation_id', title: 'Операция', href: '/operations/{operation_id}' };
export function Notice({ children }: { children: ReactNode }) { return <InfoAlert>{children}</InfoAlert>; }
