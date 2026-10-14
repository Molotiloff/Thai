'use client';

import { Icon } from '@/components/ui/Icon';
import styles from './SearchInput.module.css';

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}

export function SearchInput({ value, onChange, placeholder }: SearchInputProps) {
  return (
    <label className={styles.wrap}>
      <Icon name="search" size={15} className={styles.icon} />
      <input
        type="search"
        className={styles.input}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-label={placeholder}
      />
    </label>
  );
}
