'use client';

import { useState } from 'react';
import { Icon } from '@/components/ui/Icon';
import styles from './ExpensePagination.module.css';

const PAGE_SIZES = [10, 20, 50];

/** Общая пагинация страницы (визуальная, на local state) */
export function ExpensePagination({ totalCount }: { totalCount: number }) {
  const [pageSize, setPageSize] = useState(20);
  const [page, setPage] = useState(1);

  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  const currentPage = Math.min(page, totalPages);
  const rangeEnd = Math.min(currentPage * pageSize, totalCount);

  return (
    <footer className={styles.footer}>
      <label className={styles.pageSize}>
        <span>Показать по:</span>
        <span className={styles.selectWrap}>
          <select
            className={styles.select}
            value={pageSize}
            onChange={(event) => {
              setPageSize(Number(event.target.value));
              setPage(1);
            }}
            aria-label="Записей на странице"
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
          <Icon name="chevron-down" size={13} className={styles.selectChevron} />
        </span>
      </label>

      <div className={styles.pagination}>
        <span className={styles.range}>
          {(currentPage - 1) * pageSize + 1}–{rangeEnd} из {totalCount}
        </span>
        <button
          type="button"
          className={styles.arrow}
          aria-label="Предыдущая страница"
          disabled={currentPage === 1}
          onClick={() => setPage(currentPage - 1)}
        >
          <Icon name="chevron-left" size={14} />
        </button>
        {Array.from({ length: totalPages }, (_, i) => i + 1).map((n) => (
          <button
            key={n}
            type="button"
            className={`${styles.pageButton} ${n === currentPage ? styles.pageButtonActive : ''}`}
            aria-current={n === currentPage ? 'page' : undefined}
            onClick={() => setPage(n)}
          >
            {n}
          </button>
        ))}
        <button
          type="button"
          className={styles.arrow}
          aria-label="Следующая страница"
          disabled={currentPage === totalPages}
          onClick={() => setPage(currentPage + 1)}
        >
          <Icon name="chevron-right" size={14} />
        </button>
      </div>
    </footer>
  );
}
