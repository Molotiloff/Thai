import type { IUserService } from './interfaces';
import type { CurrentUser } from '@/types/domain';

/** Мок-реализация текущего пользователя для локальной демонстрации всех разделов CRM */
export class MockUserService implements IUserService {
  async getCurrentUser(): Promise<CurrentUser> {
    return {
      name: 'Алексей Смирнов',
      role: 'admin',
      roleLabel: 'Администратор',
      initials: 'АС',
    };
  }
}
