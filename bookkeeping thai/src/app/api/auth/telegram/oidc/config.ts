const CALLBACK_PATH = '/api/auth/telegram/oidc/callback';

export const TELEGRAM_AUTHORIZE_URL = 'https://oauth.telegram.org/auth';
export const OIDC_COOKIE_MAX_AGE = 10 * 60;
export const OIDC_COOKIES = {
  state: 'thai_oidc_state',
  nonce: 'thai_oidc_nonce',
  verifier: 'thai_oidc_verifier',
} as const;

export interface TelegramOidcConfig {
  clientId: string;
  origin: string;
  redirectUri: string;
  secureCookies: boolean;
}

export function getTelegramOidcConfig(): TelegramOidcConfig | null {
  const clientId = process.env.THAI_TELEGRAM_OIDC_CLIENT_ID?.trim() ?? '';
  const rawOrigin = process.env.THAI_FRONTEND_ORIGIN?.trim() ?? '';
  if (!/^\d+$/.test(clientId) || !rawOrigin) return null;

  try {
    const url = new URL(rawOrigin);
    if (url.username || url.password || url.search || url.hash || url.pathname !== '/' || (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost','127.0.0.1'].includes(url.hostname)))) return null;
    const origin = url.origin;
    return {
      clientId,
      origin,
      redirectUri: `${origin}${CALLBACK_PATH}`,
      secureCookies: origin.startsWith('https://'),
    };
  } catch {
    return null;
  }
}
