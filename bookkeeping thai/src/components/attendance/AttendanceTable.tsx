'use client';

import type { AttendanceDay, AttendanceStatus, Employee } from '@/types/attendance';
import { AttendanceEmployeeRow } from './AttendanceEmployeeRow';
import styles from './AttendanceTable.module.css';

interface AttendanceTableProps {
  employees: Employee[];
  days: AttendanceDay[];
}

export function AttendanceTable({ employees, days }: AttendanceTableProps) {
  const handleCellClick = (employeeId: string, day: number, status: AttendanceStatus) => {
    // TODO: открыть модальное окно редактирования статуса/комментария
    console.log(employeeId, day, status);
  };

  return (
    <section className={styles.card}>
      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={`${styles.employeeHead} ${styles.stickyCol}`}>Сотрудник</th>
              {days.map((day) => (
                <th
                  key={day.day}
                  className={`${styles.dayHead} ${day.isWeekend ? styles.weekend : ''}`}
                >
                  <span className={styles.dayNumber}>{day.day}</span>
                  <span className={styles.dayWeekday}>{day.weekday}</span>
                </th>
              ))}
              <th className={styles.moreHead} aria-label="Остальные дни месяца">…</th>
              <th className={styles.overtimeHead}>Переработка</th>
            </tr>
          </thead>
          <tbody>
            {employees.map((employee) => (
              <AttendanceEmployeeRow
                key={employee.id}
                employee={employee}
                days={days}
                onCellClick={handleCellClick}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
