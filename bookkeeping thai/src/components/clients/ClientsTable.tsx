'use client';

import { useState } from 'react';
import Link from 'next/link';
import type { Client } from '@/types/clients';
import { formatUsdt } from '@/lib/format';
import { avatarGradient } from '@/lib/avatar';
import { Icon } from '@/components/ui/Icon';
import { telegramChatLink } from '@/lib/telegramChatLink';
import styles from './ClientsTable.module.css';

const PAGE_SIZES = [10, 20, 50];

interface ClientsTableProps {
  clients: Client[];
  totalClients: number;
  selectedId: string | null;
  onSelect: (clientId: string) => void;
}

export function ClientsTable({ clients, totalClients, selectedId, onSelect }: ClientsTableProps) {
  const [pageSize, setPageSize] = useState(10);
  const [page, setPage] = useState(1);

  const totalPages = Math.max(1, Math.ceil(totalClients / pageSize));
  const currentPage = Math.min(page, totalPages);
  const visibleClients = clients.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const rangeStart = totalClients === 0 ? 0 : (currentPage - 1) * pageSize + 1;
  const rangeEnd = Math.min(currentPage * pageSize, totalClients);
  const pageNumbers =
    totalPages <= 5 ? Array.from({ length: totalPages }, (_, i) => i + 1) : [1, 2, 3, null, totalPages];

  return (
    <section className={styles.card}>
      <h2 className={styles.heading}>Список клиентов</h2>

      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.firstHead}>Клиент</th>
              <th>Telegram</th>
              <th className={styles.num}>Операций</th>
              <th className={styles.num}>Оборот</th>
              <th className={styles.lastHead}>Группа</th>
            </tr>
          </thead>
          <tbody>
            {visibleClients.map((client) => {
              const chatHref = telegramChatLink({
                chatId: client.telegramChatId,
                username: client.telegramUsername,
                inviteLink: client.telegramInviteLink,
              });
              return (
              <tr
                key={client.id}
                className={`${styles.row} ${client.id === selectedId ? styles.rowSelected : ''}`}
                onClick={() => onSelect(client.id)}
                tabIndex={0}
                onKeyDown={event=>{if(event.target!==event.currentTarget)return;if(event.key==='Enter'||event.key===' '){event.preventDefault();onSelect(client.id);}}}
                aria-selected={client.id === selectedId}
              >
                <td className={styles.firstCol}>
                  <div className={styles.client}>
                    <span
                      className={styles.avatar}
                      style={{ background: avatarGradient(client.id) }}
                      aria-hidden="true"
                    >
                      {client.initials}
                    </span>
                    <div className={styles.clientInfo}>
                      <Link href={`/clients/${encodeURIComponent(client.id)}`} className={styles.clientName}>
                        {client.name}
                      </Link>
                      <span className={styles.clientNumber}>{client.clientNumber}</span>
                    </div>
                  </div>
                </td>

                <td>
                  <div className={styles.telegram}>
                    <span className={styles.telegramIcon}>
                      <Icon name="telegram" size={13} />
                    </span>
                    <div className={styles.telegramInfo}>
                      <span className={styles.telegramUsername}>{client.telegramUsername}</span>
                      <span className={styles.telegramChatId}>{client.telegramChatId}</span>
                    </div>
                    {chatHref ? <a
                      className={styles.telegramOpen}
                      title="Открыть чат"
                      aria-label={`Открыть Telegram-чат с ${client.name}`}
                      href={chatHref}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(event) => event.stopPropagation()}
                    >
                      <Icon name="external" size={13} />
                    </a> : null}
                  </div>
                </td>

                <td className={styles.num}>{client.dealsCount}</td>
                <td className={`${styles.num} ${styles.turnover}`}>{formatUsdt(client.turnoverUsdt)}</td>
                <td className={styles.lastCol}>{client.groupName ?? "Баланс"}{client.isActive===false?" · Закрыт":""}</td>
              </tr>
              );
            })}
            {clients.length === 0 ? (
              <tr>
                <td colSpan={5} className={styles.emptyCell}>
                  Клиенты не найдены — измените запрос.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <footer className={styles.footer}>
        <label className={styles.pageSize}>
          <span>Показать по:</span>
          <span className={styles.pageSizeSelectWrap}>
            <select
              className={styles.pageSizeSelect}
              value={pageSize}
              onChange={(event) => {
                setPageSize(Number(event.target.value));
                setPage(1);
              }}
              aria-label="Клиентов на странице"
            >
              {PAGE_SIZES.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
            <Icon name="chevron-down" size={13} className={styles.pageSizeChevron} />
          </span>
        </label>

        <div className={styles.pagination}>
          <span className={styles.range}>
            {rangeStart}–{rangeEnd} из {totalClients.toLocaleString('ru-RU')}
          </span>
          <button
            type="button"
            className={styles.pageArrow}
            aria-label="Предыдущая страница"
            disabled={currentPage === 1}
            onClick={() => setPage(currentPage - 1)}
          >
            <Icon name="chevron-left" size={14} />
          </button>
          {pageNumbers.map((n, i) =>
            n === null ? (
              <span key={`gap-${i}`} className={styles.pageGap}>
                …
              </span>
            ) : (
              <button
                key={n}
                type="button"
                className={`${styles.pageButton} ${n === currentPage ? styles.pageButtonActive : ''}`}
                aria-current={n === currentPage ? 'page' : undefined}
                onClick={() => setPage(n)}
              >
                {n}
              </button>
            ),
          )}
          <button
            type="button"
            className={styles.pageArrow}
            aria-label="Следующая страница"
            disabled={currentPage === totalPages}
            onClick={() => setPage(currentPage + 1)}
          >
            <Icon name="chevron-right" size={14} />
          </button>
        </div>
      </footer>
    </section>
  );
}
