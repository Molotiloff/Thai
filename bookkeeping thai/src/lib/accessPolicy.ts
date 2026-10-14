import type { UserRole } from '@/types/domain';

export type RouteKey =
  | '/'
  | '/deals'
  | '/clients'
  | '/balances'
  | '/accounting'
  | '/expenses'
  | '/attendance'
  | '/turnover';

export const ROLES = {
  all: ['admin', 'owner', 'accountant', 'manager', 'cashier'],
  managerPlus: ['admin', 'owner', 'accountant', 'manager'],
} as const satisfies Record<string, readonly UserRole[]>;

export const routeAccess: Record<RouteKey, readonly UserRole[]> = {
  '/': ROLES.managerPlus,
  '/deals': ROLES.all,
  '/clients': ROLES.all,
  '/balances': ROLES.all,
  '/accounting': ROLES.managerPlus,
  '/expenses': ROLES.managerPlus,
  '/attendance': ROLES.managerPlus,
  '/turnover': ROLES.managerPlus,
};

export function canAccessRoute(role: UserRole, route: RouteKey): boolean {
  return routeAccess[route].includes(role);
}
