import type { IconName } from '@/components/ui/Icon';

/**
 * Типы раздела «Посещаемость»: календарь статусов сотрудников за месяц,
 * сводные метрики и легенда. Данные приходят через IAttendanceService.
 */

export type AttendanceStatus =
  | 'work'
  | 'absent'
  | 'flight'
  | 'remote';

export type EmployeeRole = 'Менеджер' | 'Оператор' | 'Кассир';

export interface Employee {
  id: string;
  name: string;
  role: EmployeeRole;
  avatarUrl?: string;
  /** Часы переработки за месяц; null — переработок нет */
  overtimeHours: number | null;
  /** Статусы по дням месяца; отсутствие дня = нет данных (будущая дата) */
  attendance: Record<number, AttendanceStatus>;
}

export interface AttendanceDay {
  day: number;
  weekday: string;
  isWeekend: boolean;
}

export type SummaryTone = 'blue' | 'green' | 'orange' | 'red';

export interface SummaryMetric {
  id: string;
  title: string;
  value: string | number;
  subtitle: string;
  icon: IconName;
  tone: SummaryTone;
}

/** Снимок месяца посещаемости — единица ответа сервиса */
export interface AttendanceMonth {
  monthLabel: string;
  days: AttendanceDay[];
  employees: Employee[];
  summary: SummaryMetric[];
}
