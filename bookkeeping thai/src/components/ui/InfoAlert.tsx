import { Icon } from '@/components/ui/Icon';
import styles from './InfoAlert.module.css';

export function InfoAlert({ children }: { children: React.ReactNode }) {
  return (
    <aside className={styles.alert}>
      <span className={styles.iconWrap}>
        <Icon name="info" size={15} />
      </span>
      <p>{children}</p>
    </aside>
  );
}
