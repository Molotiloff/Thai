import type { NextRequest } from 'next/server';
// Compare with the browser-facing Host, not Next's internal listen hostname.
export function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get('origin');
  if (!origin) return true;
  try {
    const parsed = new URL(origin);
    return parsed.host === request.headers.get('host') && parsed.protocol === request.nextUrl.protocol;
  } catch { return false; }
}

export function browserUrl(path: string, request: NextRequest): URL {
  const url = new URL(path, request.url);
  const host = request.headers.get('host');
  if (host) {
    url.port = '';
    url.host = host;
  }
  return url;
}
