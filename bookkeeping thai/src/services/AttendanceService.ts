import type { IAttendanceService } from './interfaces';
import type {
  AttendanceDay,
  AttendanceMonth,
  AttendanceStatus,
  Employee,
  SummaryMetric,
} from '@/types/attendance';

/** Дни 1–14 мая 2024 (1 мая — среда); сб/вс подсвечиваются как выходные */
const DAYS: AttendanceDay[] = [
  { day: 1, weekday: 'ср', isWeekend: false },
  { day: 2, weekday: 'чт', isWeekend: false },
  { day: 3, weekday: 'пт', isWeekend: false },
  { day: 4, weekday: 'сб', isWeekend: true },
  { day: 5, weekday: 'вс', isWeekend: true },
  { day: 6, weekday: 'пн', isWeekend: false },
  { day: 7, weekday: 'вт', isWeekend: false },
  { day: 8, weekday: 'ср', isWeekend: false },
  { day: 9, weekday: 'чт', isWeekend: false },
  { day: 10, weekday: 'пт', isWeekend: false },
  { day: 11, weekday: 'сб', isWeekend: true },
  { day: 12, weekday: 'вс', isWeekend: true },
  { day: 13, weekday: 'пн', isWeekend: false },
  { day: 14, weekday: 'вт', isWeekend: false },
];

/** Заполняет диапазон дней одним статусом (дни без записи = нет данных) */
function fill(
  from: number,
  to: number,
  status: AttendanceStatus,
  overrides: Record<number, AttendanceStatus> = {},
): Record<number, AttendanceStatus> {
  const result: Record<number, AttendanceStatus> = {};
  for (let day = from; day <= to; day += 1) {
    result[day] = status;
  }
  return { ...result, ...overrides };
}

/** Мок-реализация календаря посещаемости (данные до «текущей» даты — 10 мая) */
export class MockAttendanceService implements IAttendanceService {
  async getMonth(): Promise<AttendanceMonth> {
    const employees: Employee[] = [
      {
        id: 'emp-1',
        name: 'Ваня Я',
        role: 'Менеджер',
        overtimeHours: 9.5,
        attendance: fill(1, 10, 'work', { 2: 'remote', 7: 'flight' }),
      },
      {
        id: 'emp-2',
        name: 'Никита',
        role: 'Менеджер',
        overtimeHours: null,
        attendance: fill(1, 10, 'work', { 4: 'remote' }),
      },
      {
        id: 'emp-3',
        name: 'Монах',
        role: 'Оператор',
        overtimeHours: null,
        attendance: fill(1, 10, 'work', {
          5: 'remote',
          9: 'flight',
        }),
      },
      {
        id: 'emp-4',
        name: 'Анита',
        role: 'Кассир',
        overtimeHours: null,
        attendance: fill(1, 10, 'work', {
          8: 'remote',
          10: 'absent',
        }),
      },
      {
        id: 'emp-5',
        name: 'Серега',
        role: 'Менеджер',
        overtimeHours: null,
        attendance: fill(1, 10, 'work', { 6: 'remote' }),
      },
      {
        id: 'emp-6',
        name: 'Лев',
        role: 'Оператор',
        overtimeHours: null,
        attendance: fill(1, 10, 'work', { 3: 'absent' }),
      },
      {
        id: 'emp-7',
        name: 'Рая',
        role: 'Оператор',
        overtimeHours: null,
        attendance: fill(1, 10, 'work'),
      },
      {
        id: 'emp-8',
        name: 'Миша',
        role: 'Оператор',
        overtimeHours: null,
        attendance: fill(1, 10, 'work', { 7: 'remote' }),
      },
      {
        id: 'emp-9',
        name: 'Саша',
        role: 'Оператор',
        overtimeHours: null,
        attendance: fill(1, 10, 'work', { 2: 'flight' }),
      },
    ];

    const summary: SummaryMetric[] = [
      {
        id: 'total',
        title: 'Всего сотрудников',
        value: employees.length,
        subtitle: 'Активных сотрудников',
        icon: 'clients',
        tone: 'blue',
      },
      {
        id: 'work',
        title: 'Рабочие дни',
        value: 83,
        subtitle: 'дней',
        icon: 'check-circle',
        tone: 'green',
      },
      {
        id: 'remote',
        title: 'Дистант',
        value: 6,
        subtitle: 'дней',
        icon: 'chat',
        tone: 'orange',
      },
      {
        id: 'overtime',
        title: 'Переработки',
        value: '9.5',
        subtitle: 'часов',
        icon: 'clock',
        tone: 'blue',
      },
      {
        id: 'absent',
        title: 'Отсутствовали',
        value: 2,
        subtitle: 'дня',
        icon: 'x-circle',
        tone: 'red',
      },
    ];

    return { monthLabel: 'Май 2024', days: DAYS, employees, summary };
  }
}
