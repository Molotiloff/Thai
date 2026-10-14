'use client';

import type { ClientComment } from '@/types/clients';
import { Icon } from '@/components/ui/Icon';
import styles from './ClientComments.module.css';

export function ClientComments({ comments }: { comments: ClientComment[] }) {
  const handleAdd = () => {
    // TODO: форма добавления комментария (API)
    console.log('add comment');
  };

  const handleDelete = (commentId: string) => {
    // TODO: удаление комментария через API
    console.log('delete comment', commentId);
  };

  return (
    <section className={styles.section}>
      <h3 className={styles.title}>Комментарии</h3>

      <ul className={styles.list}>
        {comments.map((comment) => (
          <li key={comment.id} className={styles.item}>
            <div className={styles.itemHead}>
              <span className={styles.date}>{comment.date}</span>
              <span className={styles.author}>{comment.author}</span>
              <button
                type="button"
                className={styles.deleteButton}
                title="Удалить комментарий"
                aria-label="Удалить комментарий"
                onClick={() => handleDelete(comment.id)}
              >
                <Icon name="trash" size={13} />
              </button>
            </div>
            <p className={styles.text}>{comment.text}</p>
          </li>
        ))}
        {comments.length === 0 ? <li className={styles.empty}>Комментариев пока нет</li> : null}
      </ul>

      <button type="button" className={styles.addButton} onClick={handleAdd}>
        <Icon name="plus" size={14} />
        Добавить комментарий
      </button>
    </section>
  );
}
