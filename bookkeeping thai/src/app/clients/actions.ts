'use server';

import { clientsService } from '@/services';
import { session } from '@/thai/server';
import { can } from '@/thai/types';

export async function saveTelegramInviteLink(
  clientId: string,
  inviteLink: string | null,
): Promise<{ ok: true } | { ok: false; message: string }> {
  try {
    const user = await session();
    if (!can(user,'client.manage')) {
      return { ok: false, message: 'Недостаточно прав для изменения ссылки' };
    }
    await clientsService.updateTelegramInviteLink(clientId, inviteLink);
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : 'Не удалось сохранить ссылку',
    };
  }
}
