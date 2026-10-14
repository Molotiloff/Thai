'use client';

import { useId, useState } from 'react';
import controls from './formControls.module.css';

interface Option { id: string; name: string; chatId?: string }

export function SearchableSelect({
  value,
  options,
  onChange,
  placeholder,
  label,
}: {
  value: string;
  options: Option[];
  onChange: (id: string) => void;
  placeholder: string;
  label: string;
}) {
  const listId = useId();
  const [search, setSearch] = useState('');
  const selected = options.find((option) => option.id === value);
  const display = (option: Option) =>
    `${option.name} · ${option.chatId ? `chat_id ${option.chatId}` : `ID ${option.id}`}`;

  return (
    <span className={controls.control}>
      <input
        type="text"
        className={controls.input}
        list={listId}
        value={selected ? display(selected) : search}
        onChange={(event) => {
          const next = event.target.value;
          const found = options.find((option) => display(option) === next);
          setSearch(found ? '' : next);
          onChange(found?.id ?? '');
        }}
        placeholder={placeholder}
        aria-label={label}
        autoComplete="off"
      />
      <datalist id={listId}>
        {options.map((option) => (
          <option key={option.id} value={display(option)} />
        ))}
      </datalist>
    </span>
  );
}
