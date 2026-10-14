import { Icon } from '@/components/ui/Icon';
import { ATTENDANCE_STATUS_META, ATTENDANCE_STATUS_ORDER } from './attendanceStatus';
import cellStyles from './AttendanceStatusCell.module.css';
import styles from './AttendanceLegend.module.css';

export function AttendanceLegend() {
  return (
    <ul className={styles.legend}>
      {ATTENDANCE_STATUS_ORDER.map((status) => {
        const meta = ATTENDANCE_STATUS_META[status];
        return (
          <li key={status} className={styles.item}>
            <span className={`${styles.badge} ${cellStyles[meta.modifier]}`}>
              <Icon name={meta.icon} size={13} />
            </span>
            <span>{meta.label}</span>
          </li>
        );
      })}
    </ul>
  );
}
