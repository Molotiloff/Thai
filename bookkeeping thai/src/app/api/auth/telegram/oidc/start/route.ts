import { createHash, randomBytes } from 'node:crypto';
import { NextRequest, NextResponse } from 'next/server';
import { browserUrl } from '@/thai/origin';
import {
  getTelegramOidcConfig,
  OIDC_COOKIE_MAX_AGE,
  TELEGRAM_AUTHORIZE_URL,
} from '../config';
import { ATTEMPT_PREFIX, MAX_PENDING_ATTEMPTS, attemptCookieName, encodeAttempt } from '../attempt';

export function GET(request: NextRequest) {
  const config = getTelegramOidcConfig();
  if (!config) {
    return NextResponse.redirect(browserUrl('/login?error=configuration', request));
  }

  // Set cookies on the same host Telegram will return to.
  if (request.headers.get('host') !== new URL(config.origin).host) {
    const response = NextResponse.redirect(new URL('/api/auth/telegram/oidc/start', config.origin));
    response.headers.set('Cache-Control', 'no-store');
    return response;
  }

  const state = randomBytes(32).toString('base64url');
  const nonce = randomBytes(32).toString('base64url');
  const verifier = randomBytes(48).toString('base64url');
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  const authorizeUrl = new URL(TELEGRAM_AUTHORIZE_URL);
  authorizeUrl.search = new URLSearchParams({
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    response_type: 'code',
    scope: 'openid profile',
    state,
    nonce,
    code_challenge: challenge,
    code_challenge_method: 'S256',
  }).toString();

  const response = NextResponse.redirect(authorizeUrl);
  response.headers.set('Cache-Control','no-store');
  const cookieOptions = {
    httpOnly: true,
    sameSite: 'lax' as const,
    secure: config.secureCookies,
    path: '/',
    maxAge: OIDC_COOKIE_MAX_AGE,
  };
  const pending = request.cookies.getAll().filter(cookie => cookie.name.startsWith(ATTEMPT_PREFIX));
  for (const cookie of pending.slice(0, Math.max(0, pending.length - MAX_PENDING_ATTEMPTS + 1))) {
    response.cookies.set(cookie.name, '', { ...cookieOptions, maxAge: 0 });
  }
  response.cookies.set(attemptCookieName(state)!, encodeAttempt({ state, nonce, verifier }), cookieOptions);
  return response;
}
