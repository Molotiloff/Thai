'use client';

import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import Link from 'next/link';
import type { CurrentUser } from '@/types/domain';
import type { NavItem } from './navigation';
import { Icon } from '@/components/ui/Icon';
import { UserCard } from './UserCard';
import styles from './Sidebar.module.css';

interface SidebarProps {
  items: NavItem[];
  user: CurrentUser;
}

export function Sidebar({ items, user }: SidebarProps) {
  const pathname = usePathname();
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsOpen(false);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [isOpen]);

  return (
    <>
      <div className={styles.mobileBar}>
        <button
          type="button"
          className={styles.menuButton}
          aria-label="Открыть меню"
          aria-controls="crm-navigation"
          aria-expanded={isOpen}
          onClick={() => setIsOpen(true)}
        >
          <span aria-hidden="true">☰</span>
        </button>
        <span className={styles.mobileBrand}>SkyEx</span>
      </div>
      {isOpen ? (
        <button
          type="button"
          className={styles.backdrop}
          aria-label="Закрыть меню"
          onClick={() => setIsOpen(false)}
        />
      ) : null}
      <aside id="crm-navigation" className={`${styles.sidebar} ${isOpen ? styles.sidebarOpen : ''}`}>
        <div className={styles.logo}>
          <span className={styles.logoMark} aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <path d="M4 12.5 10 3l6 9.5-6 4.5-6-4.5Z" fill="currentColor" opacity="0.9" />
              <path d="M4 12.5 10 10l6 2.5L10 17l-6-4.5Z" fill="#fff" opacity="0.35" />
            </svg>
          </span>
          <span className={styles.logoText}>SkyEx</span>
          <button
            type="button"
            className={styles.closeButton}
            aria-label="Закрыть меню"
            onClick={() => setIsOpen(false)}
          >
            <Icon name="x" size={20} />
          </button>
        </div>

        <nav className={styles.nav} aria-label="Основная навигация">
          {items.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`${styles.navItem} ${isActive ? styles.navItemActive : ''}`}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => setIsOpen(false)}
              >
                <Icon name={item.icon} size={18} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <UserCard user={user} />
      </aside>
    </>
  );
}
