'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ActionForm } from '@/thai/forms';
import { labels, can, type Session } from '@/thai/types';
import type { Client } from '@/types/clients';
import { formatCrypto, formatUsdt } from '@/lib/format';
import { telegramChatLink } from '@/lib/telegramChatLink';
import { avatarGradient } from '@/lib/avatar';
import { Icon } from '@/components/ui/Icon';
import { ClientComments } from './ClientComments';
import styles from './ClientInfoPanel.module.css';

function ClientContactBlock({ client, canEdit }: { client: Client; canEdit: boolean }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(client.telegramInviteLink ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState<{value:string|null;key:string}|null>(null);
  const chatHref = telegramChatLink({
    chatId: client.telegramChatId,
    username: client.telegramUsername,
    inviteLink: client.telegramInviteLink,
  });

  const handleCopyChatId = () => {
    void navigator.clipboard.writeText(client.telegramChatId);
  };

  const handleSaveInvite = async () => {
    setSaving(true);
    setError('');
    const value=draft.trim()||null;
    const request=attempt?.value===value?attempt:{value,key:crypto.randomUUID()};setAttempt(request);
    try {
      const response=await fetch(`/api/thai/clients/${client.id}/telegram-link`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({invite_link:value,idempotency_key:request.key})});
      const result=await response.json();if(!response.ok)throw new Error(result.error?.message??'Не удалось сохранить ссылку');
      setEditing(false);router.refresh();
    } catch(error){setError(error instanceof Error?error.message:'Ошибка сети');}
    finally {setSaving(false);}

  };

  return (
    <div className={styles.contactBlock}>
      <div className={styles.contactRow}>
        <span className={styles.contactIcon}>
          <Icon name="telegram" size={13} />
        </span>
        <span className={styles.contactValue}>{client.telegramUsername || 'Telegram-чат'}</span>
        {chatHref ? <a
          className={styles.contactAction}
          title="Открыть чат"
          aria-label="Открыть Telegram-чат"
          href={chatHref}
          target="_blank"
          rel="noopener noreferrer"
        >
          <Icon name="external" size={13} />
        </a> : null}
      </div>
      <div className={styles.contactRow}>
        <span className={styles.contactIcon}>
          <Icon name="chat" size={13} />
        </span>
        <span className={`${styles.contactValue} ${styles.chatId}`}>{client.telegramChatId}</span>
        <button
          type="button"
          className={styles.contactAction}
          title="Скопировать chat_id"
          aria-label="Скопировать chat_id"
          onClick={handleCopyChatId}
        >
          <Icon name="copy" size={13} />
        </button>
      </div>
      {canEdit ? (
        editing ? (
          <div className={styles.inviteEditor}>
            <label htmlFor={`invite-${client.id}`}>Ссылка Telegram для открытия в приложении</label>
            <input
              id={`invite-${client.id}`}
              type="url"
              value={draft}
              placeholder="https://t.me/+..."
              onChange={(event) => setDraft(event.target.value)}
              disabled={saving}
            />
            {error ? <span className={styles.inviteError} role="alert">{error}</span> : null}
            <div className={styles.inviteActions}>
              <button type="button" disabled={saving} onClick={() => void handleSaveInvite()}>
                Сохранить
              </button>
              <button type="button" disabled={saving} onClick={() => {
                setDraft(client.telegramInviteLink ?? '');
                setError('');
                setEditing(false);
              }}>
                Отмена
              </button>
            </div>
          </div>
        ) : (
          <button type="button" className={styles.inviteEditButton} onClick={() => {
            setDraft(client.telegramInviteLink ?? '');
            setError('');
            setEditing(true);
          }}>
            {client.telegramInviteLink ? 'Изменить ссылку Telegram' : 'Добавить ссылку Telegram'}
          </button>
        )
      ) : null}
    </div>
  );
}

function ClientMainInfo({ client }: { client: Client }) {
  const rows = [
    { label: 'Дата регистрации', value: client.registrationDate },
    { label: 'Последний оператор', value: client.managerName },
    { label: 'Группа', value: client.groupName ?? 'Баланс' },
    { label: 'Статус', value: client.isActive===false?'Закрыт':'Активен' },

  ];

  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>Основная информация</h3>
      <dl className={styles.infoRows}>
        {rows.map((row) => (
          <div key={row.label} className={styles.infoRow}>
            <dt className={styles.infoLabel}>{row.label}</dt>
            <dd className={styles.infoValue}>{row.value}</dd>
          </div>
        ))}
      </dl>
      {client.comment ? <p className={styles.commentNote}>{client.comment}</p> : null}
    </section>
  );
}

function ClientBalances({ client }: { client: Client }) {
  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>Балансы</h3>
      <dl className={styles.infoRows}>
        {client.balances.map((balance) => (
          <div key={balance.currency} className={styles.infoRow}>
            <dt className={styles.infoLabel}>{labels[balance.currency]??balance.currency}</dt>
            <dd className={styles.infoValue}>
              {formatCrypto(balance.amount, balance.currency)}
              {!!balance.unvaluedAmount && <small> · без себестоимости: {formatCrypto(balance.unvaluedAmount, balance.currency)}</small>}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function ClientStats({ client }: { client: Client }) {
  const cells = [
    { label: 'Операций всего', value: String(client.dealsCount) },
    { label: 'Оборот', value: formatUsdt(client.turnoverUsdt) },
    { label: 'Объём покупки', value: formatUsdt(client.purchaseVolumeUsdt) },
    { label: 'Объём продажи', value: formatUsdt(client.saleVolumeUsdt) },
    { label: 'Средний чек', value: formatUsdt(client.averageCheckUsdt) },
  ];

  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>Статистика клиента</h3>
      <div className={styles.statsGrid}>
        {cells.map((cell) => (
          <div key={cell.label} className={styles.statCell}>
            <span className={styles.statLabel}>{cell.label}</span>
            <span className={styles.statValue}>{cell.value}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function ClientRecentDeals({ client }: { client: Client }) {

  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>Последние операции</h3>
      <ul className={styles.dealsList}>
        {client.recentDeals.map((deal) => (
          <li key={deal.id}>
            <Link href={deal.href??`/deals/${deal.id}`} className={styles.dealRow}>
              <span className={styles.dealId}>{deal.id}</span>
              <span className={styles.dealInfo}>
                {deal.type} {deal.direction}{deal.status&&deal.status!=='posted'?` · ${labels[deal.status]??deal.status}`:''}
              </span>
              <span className={styles.dealAmount}>{formatUsdt(deal.amountUsdt)}</span>
              <span className={styles.dealTime}>{deal.time}</span>
            </Link>
          </li>
        ))}
      </ul>
      <Link href={`/deals?query=${encodeURIComponent(client.name)}`} className={styles.allDealsLink}>
        Все сделки клиента
        <Icon name="chevron-right" size={14} />
      </Link>
    </section>
  );
}

interface ClientInfoPanelProps {
  client: Client;
  onClose: () => void;
  canEditTelegramLink: boolean;
  session: Session;
}

export function ClientInfoPanel({ client, onClose, canEditTelegramLink, session }: ClientInfoPanelProps) {
  return (
    <aside className={styles.panel} aria-label="Информация о клиенте">
      <header className={styles.header}>
        <h2 className={styles.heading}>Информация о клиенте</h2>
        <button
          type="button"
          className={styles.closeButton}
          aria-label="Закрыть панель"
          onClick={onClose}
        >
          <Icon name="x" size={15} />
        </button>
      </header>

      <div className={styles.identity}>
        <span
          className={styles.avatar}
          style={{ background: avatarGradient(client.id) }}
          aria-hidden="true"
        >
          {client.initials}
        </span>
        <div>
          <span className={styles.name}>{client.name}</span>
          <span className={styles.number}>{client.clientNumber}</span>
        </div>
      </div>

      <ClientContactBlock key={client.id} client={client} canEdit={canEditTelegramLink} />
      <ClientMainInfo client={client} />
      <ClientBalances client={client} />
      <ClientStats client={client} />
      <ClientRecentDeals client={client} />
      {client.comments.length ? <ClientComments comments={client.comments} /> : null}
      {client.isActive && can(session,'client.manage') ? <section className={styles.section}>
       <h3 className={styles.sectionTitle}>Управление клиентом</h3>
       <ActionForm key={client.id} spec={{title:'Изменить группу',path:`/clients/${client.id}/group`,method:'PUT',permission:'client.manage',financial:false,fields:[{name:'group_code',label:'Группа',value:client.groupCode,options:[['balance','Баланс'],['partner','Партнер']]},{name:'reason',label:'Причина изменения'}]}} submitLabel="Сохранить группу"/>
       <details><summary>Привязать Telegram-чат</summary><ActionForm spec={{title:'Привязать чат',path:`/clients/${client.id}/chat`,permission:'client.manage',financial:false,fields:[{name:'chat_id',label:'Chat ID'}]}} submitLabel="Привязать"/></details>
      </section> : null}
    </aside>
  );
}
