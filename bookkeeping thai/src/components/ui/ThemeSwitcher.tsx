'use client';

import { useTheme } from '@/theme/ThemeProvider';
import { Icon } from './Icon';
import styles from './ThemeSwitcher.module.css';

export function ThemeSwitcher() {
  const { theme, toggleTheme } = useTheme();
  const label = theme === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему';

  return (
    <button
      type="button"
      className={styles.switcher}
      onClick={toggleTheme}
      aria-label={label}
      title={label}
    >
      <span className={styles.icon}>
        <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={16} />
      </span>
    </button>
  );
}
