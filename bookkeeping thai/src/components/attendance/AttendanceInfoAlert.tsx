import { Icon } from '@/components/ui/Icon';
import styles from './AttendanceInfoAlert.module.css';

export function AttendanceInfoAlert() {
  return (
    <aside className={styles.alert}>
      <span className={styles.iconWrap}>
        <Icon name="info" size={15} />
      </span>
      <p>Нажмите на ячейку в календаре, чтобы добавить комментарий или отредактировать статус.</p>
    </aside>
  );
}
