'use client';

import { useEffect, useRef } from 'react';
import { usePathname, useRouter } from 'next/navigation';

interface DealsRealtimeRefreshProps {
  wsUrl: string;
  pollingIntervalMs?: number;
}

const DEAL_EVENT_PREFIX = 'deal.';

export function DealsRealtimeRefresh({
  wsUrl,
  pollingIntervalMs = 5_000,
}: DealsRealtimeRefreshProps) {
  const pathname = usePathname();
  const router = useRouter();
  const lastRefreshAt = useRef(0);

  useEffect(() => {
    if (!pathname.startsWith('/deals')) return;

    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let failedConnections = 0;

    const refresh = () => {
      const now = Date.now();
      if (now - lastRefreshAt.current < 500) return;
      lastRefreshAt.current = now;
      router.refresh();
    };

    const pollingTimer = setInterval(refresh, pollingIntervalMs);
    const endpoint = dealsWebSocketUrl(wsUrl);

    const connect = () => {
      if (!endpoint || disposed) return;

      socket = new WebSocket(endpoint);
      socket.onopen = () => {
        failedConnections = 0;
      };
      socket.onmessage = ({ data }) => {
        try {
          const event = JSON.parse(String(data)) as { type?: unknown };
          if (typeof event.type === 'string' && event.type.startsWith(DEAL_EVENT_PREFIX)) {
            refresh();
          }
        } catch {
          // Ignore malformed messages and keep the connection alive.
        }
      };
      socket.onclose = ({ code }) => {
        if (disposed || code === 1000 || code === 1008) return;
        failedConnections += 1;
        if (failedConnections >= 5) return;
        const delay = Math.min(2_000 * 2 ** (failedConnections - 1), 30_000);
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      disposed = true;
      clearInterval(pollingTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [pathname, pollingIntervalMs, router, wsUrl]);

  return null;
}

function dealsWebSocketUrl(wsUrl: string): string {
  const base = wsUrl.replace(/\/+$/, '');
  if (!base) return '';
  if (base.endsWith('/ws/deals')) return base;
  if (base.endsWith('/ws')) return `${base}/deals`;
  return `${base}/ws/deals`;
}
