/** Prefer a Telegram app-capable HTTPS link; numeric-only private chats use Web fallback. */
export function telegramChatLink({
  chatId,
  username,
  inviteLink,
}: {
  chatId?: string | null;
  username?: string | null;
  inviteLink?: string | null;
}): string | null {
  const id = chatId?.trim();
  if (!id || !/^-?\d+$/.test(id)) return null;

  const invite = inviteLink?.trim();
  if (invite) {
    try {
      const url = new URL(invite);
      if (
        url.protocol === 'https:' &&
        ['t.me', 'telegram.me'].includes(url.hostname.toLowerCase()) &&
        /^\/(?:\+[A-Za-z0-9_-]+|joinchat\/[A-Za-z0-9_-]+|[A-Za-z][A-Za-z0-9_]{4,31})\/?$/.test(url.pathname) &&
        !url.search && !url.hash
      ) {
        return `https://t.me${url.pathname.replace(/\/$/, '')}`;
      }
    } catch {
      // Ignore stale or malformed invite links and use the safe fallback.
    }
  }

  const name = username?.trim().replace(/^@/, '');
  if (name && /^[A-Za-z][A-Za-z0-9_]{4,31}$/.test(name)) {
    return `https://t.me/${name}`;
  }

  return `https://web.telegram.org/k/#${id}`;
}
