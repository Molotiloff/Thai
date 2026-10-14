import { notFound } from 'next/navigation';
import { userService } from '@/services';
import type { CurrentUser } from '@/types/domain';
import { canAccessRoute, type RouteKey } from './accessPolicy';

export async function requireRouteAccess(route: RouteKey): Promise<CurrentUser> {
  const user = await userService.getCurrentUser();
  if (!canAccessRoute(user.role, route)) {
    notFound();
  }
  return user;
}
