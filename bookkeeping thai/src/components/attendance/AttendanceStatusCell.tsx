import { Icon } from '@/components/ui/Icon';
import type { AttendanceStatus } from '@/types/attendance';
import { ATTENDANCE_STATUS_META } from './attendanceStatus';
import styles from './AttendanceStatusCell.module.css';

interface AttendanceStatusCellProps {
  status?: AttendanceStatus;
  onClick?: () => void;
}

export function AttendanceStatusCell({ status, onClick }: AttendanceStatusCellProps) {
  if (!status) {
    return (
      <span className={`${styles.cell} ${styles.empty}`} title="Нет данных" aria-label="Нет данных">
        <Icon name="clock" size={16} />
      </span>
    );
  }

  const meta = ATTENDANCE_STATUS_META[status];

  return (
    <button
      type="button"
      className={`${styles.cell} ${styles[meta.modifier]}`}
      title={meta.label}
      aria-label={meta.label}
      onClick={onClick}
    >
      <Icon name={meta.icon} size={16} />
    </button>
  );
}
