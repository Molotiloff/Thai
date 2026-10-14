import type {
  DashboardShadowComparison,
  DashboardShadowReport,
} from '@/types/mainDashboard';
import styles from './ShadowReportPanel.module.css';

const CLASSIFICATION_LABELS: Record<string, string> = {
  mapping: 'Нет сопоставления',
  missing_history: 'Нет истории',
  timestamp_skew: 'Разные даты',
  sign: 'Разный знак',
  rounding: 'Округление',
  stale_external_data: 'Таблица отстаёт',
  real_accounting_error: 'Требует проверки',
};

interface ShadowReportPanelProps {
  source: string;
  comparison?: DashboardShadowComparison;
  warnings: string[];
  report: DashboardShadowReport | null;
}

export function ShadowReportPanel({
  source,
  comparison,
  warnings,
  report,
}: ShadowReportPanelProps) {
  const counts = (report?.differences ?? []).reduce<Record<string, number>>((result, item) => {
    result[item.classification] = (result[item.classification] ?? 0) + 1;
    return result;
  }, {});

  return (
    <details className={styles.card}>
      <summary className={styles.summary}>
        <span>Учёт и сверка</span>
        <strong>Подробнее</strong>
      </summary>

      <div className={styles.notices}>
        <p>Источник главной: {sourceLabel(source)}.</p>
        {comparison ? <p>{shadowStatus(comparison)}</p> : null}
        {warnings.map((warning) => (
          <p key={warning}>{warning}</p>
        ))}
      </div>

      {report ? (
        <>
          <div className={styles.meta}>
            <span>Отчёт №{report.id}</span>
            <span>Дата учёта: {formatDate(report.businessDate)}</span>
            <span>Основной источник: {sourceLabel(report.primarySource)}</span>
            <span>Сравнено полей: {report.comparedFields}</span>
          </div>

          <div className={styles.classifications}>
            {Object.entries(counts).map(([classification, count]) => (
              <span key={classification}>
                {classificationLabel(classification)}: {count}
              </span>
            ))}
          </div>

          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Показатель</th>
                  <th>Google Sheets</th>
                  <th>PostgreSQL</th>
                  <th>Разница</th>
                  <th>Причина</th>
                </tr>
              </thead>
              <tbody>
                {report.differences.map((difference) => (
                  <tr key={difference.path}>
                    <td>{pathLabel(difference.path)}</td>
                    <td>{formatDecimal(difference.sheet)}</td>
                    <td>{formatDecimal(difference.database)}</td>
                    <td>{formatDecimal(difference.absoluteDelta)}</td>
                    <td>{classificationLabel(difference.classification)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </details>
  );
}

function shadowStatus(comparison: DashboardShadowComparison): string {
  const report = comparison.reportId ? ` Отчёт №${comparison.reportId}.` : '';
  if (comparison.status === 'matched') {
    return `Shadow-сверка пройдена: совпали все ${comparison.comparedFields} показателей.${report}`;
  }
  if (comparison.status === 'unavailable') {
    return `Shadow-сверка недоступна: PostgreSQL-снимок не получен.${report}`;
  }
  return `Shadow-сверка: сопоставлено ${comparison.comparedFields} полей, создано ${comparison.mismatchCount} диагностик.${report}`;
}

function classificationLabel(value: string): string {
  return CLASSIFICATION_LABELS[value] ?? value;
}

function sourceLabel(value: string): string {
  return value === 'postgres' ? 'PostgreSQL' : value === 'sheets' ? 'Google Sheets' : value;
}

function formatDate(value: string): string {
  const [year, month, day] = value.split('-');
  return `${day}.${month}.${year}`;
}

function formatDecimal(value?: string): string {
  if (value === undefined) return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return value;
  return number.toLocaleString('ru-RU', { maximumFractionDigits: 8 });
}

function pathLabel(path: string): string {
  const labels: Record<string, string> = {
    reconciliation: 'Сверка',
    currencies: 'Валюты',
    periods: 'Периоды',
    cities: 'Города',
    rub_cash: 'RUB в кассах',
    rub_in_currency: 'RUB в валюте',
    total_rub: 'Общий RUB',
    client_balances: 'Балансы клиентов',
    skyex_balances: 'Балансы SkyEx',
    total_balances: 'Общие балансы',
    fact_turnover: 'Фактический оборот',
    accumulated_profit: 'Накопленная прибыль',
    invested_capital: 'Вложенный капитал',
    turnover: 'Оборот',
    gap: 'Разрыв',
    fact_rub: 'Факт RUB',
    free_qty: 'Свободный остаток',
    internal_rate: 'Внутренний курс',
    rub_cost: 'Себестоимость в RUB',
    physical_qty: 'Физический остаток',
    daily_income: 'Доход за день',
    daily_expense: 'Расход за день',
    daily_profit: 'Прибыль за день',
    daily_turnover: 'Оборот за день',
    monthly_turnover: 'Оборот за месяц',
    profitability: 'Доходность',
    income: 'Доход',
    expense: 'Расход',
    profit: 'Прибыль',
  };
  return path
    .split('.')
    .map((part) => labels[part] ?? part.toUpperCase())
    .join(' · ');
}
