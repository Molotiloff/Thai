/** Форматирование чисел и времени для интерфейса */

const rubFormatter = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 0,
});

export function formatRub(amount: number): string {
  return `${rubFormatter.format(amount)} ₽`;
}

import type { MoneyAmount } from '@/types/domain';

/** Форматирование суммы в произвольной валюте: 45 300 USDT, $12 500, 8,2 млн ₽ */
export function formatMoney({ currency, amount }: MoneyAmount): string {
  const formatted = amount.toLocaleString('ru-RU', { maximumFractionDigits: 3 });
  switch (currency) {
    case 'RUB':
      return `${formatted} ₽`;
    case 'USD':
      return `$${formatted}`;
    case 'EUR':
      return `€${formatted}`;
    default:
      return `${formatted} ${currency}`;
  }
}

/** 378 945.00 ₽ — пробел между разрядами, точка в дробной части */
export function formatMoneyRub(value: number): string {
  return `${groupDigits(value, 2, 2)} ₽`;
}

const CURRENCY_DECIMALS: Readonly<Record<string, number>> = {
  USDT: 2,
  RUB: 2,
  USD: 2,
  USD_BL: 2,
  USD_WH: 2,
  EUR: 2,
  BTC: 5,
  ETH: 4,
  CNY: 2,
};

/** 3 789.45 (USDT) / 0.04560 (BTC) / 1.5500 (ETH) / 782 450.00 (RUB) */
export function formatCrypto(value: number, currency: string): string {
  const decimals = CURRENCY_DECIMALS[currency] ?? 2;
  return groupDigits(value, decimals, decimals);
}

/** +3.2% / -1.5% */
export function formatPercent(value: number): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(1)}%`;
}

/** 43 396 362 — целое с разделением разрядов */
export function formatNumber(value: number): string {
  return rubFormatter.format(value);
}

/** 128 $ */
export function formatUsd(value: number): string {
  return `${rubFormatter.format(value)} $`;
}

/** -6 ₽ / +515 328 ₽ — рубли со знаком */
export function formatSignedRub(value: number): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${formatRub(value)}`;
}

function groupDigits(value: number, minFraction: number, maxFraction: number): string {
  return value
    .toLocaleString('en-US', {
      minimumFractionDigits: minFraction,
      maximumFractionDigits: maxFraction,
    })
    .replace(/,/g, ' ');
}

export function formatUsdt(amount: number): string {
  return `${amount.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} USDT`;
}
