'use client';

import { useActionState, useEffect, useRef, useState } from 'react';
import { recordManualCashAction, reverseManualCashAction, type ManualCashActionState } from '@/app/manualCashActions';
import type { ManualCashSnapshot } from '@/types/manualCash';
import styles from './ManualCashPanel.module.css';

const INITIAL: ManualCashActionState = {};

interface ManualCashPanelProps {
  snapshot: ManualCashSnapshot;
  today: string;
  submissionKey: string;
  canEdit: boolean;
}

export function ManualCashPanel({ snapshot, today, submissionKey, canEdit }: ManualCashPanelProps) {
  const reversals = new Map(
    snapshot.moves
      .filter((move) => move.reversalOfId !== null)
      .map((move) => [move.reversalOfId, move]),
  );
  const moves = snapshot.moves.filter((move) => move.operation !== 'reversal');
  return <section className={styles.card}>
    <div className={styles.heading}><div><h2>RUB Москва</h2><p>Ручные кассовые счета</p></div></div>
    <div className={styles.balances}>{snapshot.accounts.map(account => <div key={account.code}><span>{account.name}</span><strong>{money(account.balance)}</strong></div>)}</div>
    {canEdit ? <RecordForm today={today} idempotencyKey={`${submissionKey}:record`} /> : null}
    <div className={styles.moves}>{moves.map(move => <Move key={move.id} move={move} reversal={reversals.get(move.id)} idempotencyKey={`${submissionKey}:reverse:${move.id}`} canEdit={canEdit} />)}</div>
  </section>;
}

function RecordForm({ today, idempotencyKey: initialIdempotencyKey }: { today: string; idempotencyKey: string }) {
  const [state, submit, pending] = useActionState(recordManualCashAction, INITIAL);
  const formRef = useRef<HTMLFormElement>(null);
  const [idempotencyKey] = useState(initialIdempotencyKey);
  useEffect(() => {
    if (state.success) {
      formRef.current?.reset();
    }
  }, [state.success]);
  return <form ref={formRef} action={submit} className={styles.form}>
      <input type="hidden" name="idempotencyKey" value={state.nextIdempotencyKey ?? idempotencyKey} />
      <select name="accountCode" required><option value="moscow_poets">Поэты</option><option value="moscow_bs">BS</option></select>
      <select name="operation" required><option value="inflow">Приход</option><option value="outflow">Расход</option></select>
      <input name="amount" inputMode="decimal" placeholder="Сумма, ₽" required />
      <input name="effectiveAt" type="date" defaultValue={today} required />
      <input name="comment" placeholder="Комментарий" required />
      <button disabled={pending}>{pending ? 'Проводим…' : 'Провести'}</button>
      <Message state={state} />
    </form>;
}

function Move({ move, reversal, idempotencyKey, canEdit }: { move: ManualCashSnapshot['moves'][number]; reversal?: ManualCashSnapshot['moves'][number]; idempotencyKey: string; canEdit: boolean }) {
  const action = reverseManualCashAction.bind(null, move.id);
  const [state, submit, pending] = useActionState(action, INITIAL);
  return <div className={styles.move}>
    <div><strong>{move.accountCode === 'moscow_poets' ? 'Поэты' : 'BS'} · {label(move.operation)}{move.reversed ? ' · Отменено' : ''}</strong><span>{move.effectiveAt} · {move.comment} · {move.actorName}</span>{reversal ? <span>Сторно: {reversal.comment} · {reversal.actorName}</span> : null}</div>
    <b className={move.reversed ? styles.reversedAmount : undefined}>{move.operation === 'outflow' ? '−' : move.operation === 'inflow' ? '+' : ''}{money(move.amount)}</b>
    {canEdit && !move.reversed && !state.success && ['inflow', 'outflow'].includes(move.operation) ? <form action={submit} className={styles.reverse}><input type="hidden" name="idempotencyKey" value={idempotencyKey} /><input name="comment" placeholder="Причина сторно" required /><button disabled={pending}>Сторно</button></form> : null}
    <Message state={state} />
  </div>;
}

function Message({ state }: { state: ManualCashActionState }) { return <span className={state.error ? styles.error : styles.success}>{state.error ?? state.success ?? ''}</span>; }
function money(value: string) { return `${Number(value).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`; }
function label(value: string) { return ({ opening: 'Opening', inflow: 'Приход', outflow: 'Расход', reversal: 'Сторно' } as Record<string, string>)[value] ?? value; }
