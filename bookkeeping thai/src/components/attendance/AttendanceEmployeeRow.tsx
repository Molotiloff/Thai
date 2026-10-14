import type { AttendanceDay, AttendanceStatus, Employee } from '@/types/attendance';
import { avatarGradient, initialsOf } from '@/lib/avatar';
import { AttendanceStatusCell } from './AttendanceStatusCell';
import styles from './AttendanceTable.module.css';

interface AttendanceEmployeeRowProps {
  employee: Employee;
  days: AttendanceDay[];
  onCellClick: (employeeId: string, day: number, status: AttendanceStatus) => void;
}

export function AttendanceEmployeeRow({ employee, days, onCellClick }: AttendanceEmployeeRowProps) {
  return (
    <tr className={styles.row}>
      <td className={`${styles.employeeCell} ${styles.stickyCol}`}>
        <div className={styles.employee}>
          {employee.avatarUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={employee.avatarUrl} alt="" className={styles.avatar} />
          ) : (
            <span
              className={styles.avatar}
              style={{ background: avatarGradient(employee.id) }}
              aria-hidden="true"
            >
              {initialsOf(employee.name)}
            </span>
          )}
          <div className={styles.employeeInfo}>
            <span className={styles.employeeName}>{employee.name}</span>
            <span className={styles.employeeRole}>{employee.role}</span>
          </div>
        </div>
      </td>

      {days.map(({ day }) => {
        const status = employee.attendance[day];
        return (
          <td key={day} className={styles.statusCol}>
            <AttendanceStatusCell
              status={status}
              onClick={status ? () => onCellClick(employee.id, day, status) : undefined}
            />
          </td>
        );
      })}

      <td className={styles.moreCol} aria-hidden="true" />
      <td className={styles.overtimeCol}>
        {employee.overtimeHours === null ? (
          <span className={styles.overtimeEmpty}>—</span>
        ) : (
          <span className={styles.overtimeValue}>{employee.overtimeHours.toFixed(1)}</span>
        )}
      </td>
    </tr>
  );
}
