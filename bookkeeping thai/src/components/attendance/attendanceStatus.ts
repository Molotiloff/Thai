import type { IconName } from '@/components/ui/Icon';
import type { AttendanceStatus } from '@/types/attendance';

/** Визуальная мета статусов: иконка, подпись и CSS-модификатор ячейки/легенды */
export const ATTENDANCE_STATUS_META: Record<
  AttendanceStatus,
  { icon: IconName; label: string; modifier: string }
> = {
  work: { icon: 'check-circle', label: 'Рабочий день', modifier: 'work' },
  absent: { icon: 'x-circle', label: 'Пропуск', modifier: 'absent' },
  flight: { icon: 'plane', label: 'Полёт', modifier: 'flight' },
  remote: { icon: 'chat', label: 'Дистант', modifier: 'remote' },
};

export const ATTENDANCE_STATUS_ORDER: AttendanceStatus[] = [
  'work',
  'absent',
  'flight',
  'remote',
];
