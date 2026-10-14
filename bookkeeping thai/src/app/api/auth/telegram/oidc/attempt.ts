import { timingSafeEqual } from 'node:crypto';

export const ATTEMPT_PREFIX = 'thai_oidc_attempt_';
export const MAX_PENDING_ATTEMPTS = 8;
const STATE = /^[A-Za-z0-9_-]{43}$/;
export interface LoginAttempt { state: string; nonce: string; verifier: string }

export function attemptCookieName(state: string | null): string | null {
  return state && STATE.test(state) ? ATTEMPT_PREFIX + state : null;
}
export function encodeAttempt(attempt: LoginAttempt): string {
  return Buffer.from(JSON.stringify(attempt)).toString('base64url');
}
export function decodeAttempt(value: string | undefined, state: string | null): LoginAttempt | null {
  if (!value || value.length > 1024 || !attemptCookieName(state)) return null;
  try {
    const attempt = JSON.parse(Buffer.from(value, 'base64url').toString()) as Partial<LoginAttempt>;
    if (!safeEqual(state, attempt.state) || typeof attempt.nonce !== 'string' || !STATE.test(attempt.nonce)
        || typeof attempt.verifier !== 'string' || !/^[A-Za-z0-9_-]{64}$/.test(attempt.verifier)) return null;
    return attempt as LoginAttempt;
  } catch { return null; }
}
export function safeEqual(actual: string | null, expected: string | undefined): boolean {
  if (!actual || !expected) return false;
  const a = Buffer.from(actual), b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}
