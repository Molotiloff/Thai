'use client';

import type { BalanceClient } from '@/types/balances';
import { formatCrypto, formatMoneyRub } from '@/lib/format';
import { BalanceTableRow } from './BalanceTableRow';
import styles from './BalancesTable.module.css';

interface BalancesTableProps {
  clients: BalanceClient[];
}

export function BalancesTable({ clients }: BalancesTableProps) {
  const handleRowClick = (clientId: string) => {
    // TODO: открыть карточку клиента
    console.log(clientId);
  };

  const totalBalance = clients.reduce((sum, client) => sum + client.balance, 0);
  const totalRub = clients.reduce((sum, client) => sum + client.balanceRub, 0);
  const singleCurrency = clients[0]?.currency;
  const balanceTotal = singleCurrency && clients.every((client) => client.currency === singleCurrency)
    ? formatCrypto(totalBalance, singleCurrency)
    : '—';

  return (
    <section className={styles.card}>
      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.clientHead}>Клиент</th>
              <th className={styles.currencyHead}>Валюта</th>
              <th className={styles.amountHead}>Баланс</th>
              <th className={styles.amountHead}>В пересчёте в RUB</th>
              <th className={styles.chatHead}>Ссылка на чат</th>
            </tr>
          </thead>
          <tbody>
            {clients.map((client) => (
              <BalanceTableRow
                key={`${client.id}:${client.currency}`}
                client={client}
                onRowClick={handleRowClick}
              />
            ))}
            {clients.length === 0 ? (
              <tr>
                <td colSpan={5} className={styles.emptyCell}>
                  Ничего не найдено — измените фильтр или запрос.
                </td>
              </tr>
            ) : null}
          </tbody>
          <tfoot>
            <tr className={styles.totalRow}>
              <td className={styles.totalLabel}>Итого</td>
              <td className={styles.totalDash}>—</td>
              <td className={styles.totalValue}>{balanceTotal}</td>
              <td className={styles.totalValue}>{formatMoneyRub(totalRub)}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
