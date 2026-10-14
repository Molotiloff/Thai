import { getTelegramOidcConfig } from '@/app/api/auth/telegram/oidc/config';
import styles from './page.module.css';

const ERRORS: Record<string, string> = {
  access: 'Ваш Telegram-аккаунт не имеет доступа к CRM.',
  configuration: 'Вход через Telegram ещё не настроен.',
  service: 'Telegram-авторизация временно недоступна. Попробуйте ещё раз.',
  state: 'Сессия входа истекла. Начните авторизацию заново.',
};

interface LoginPageProps {
  searchParams: Promise<{ error?: string }>;
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const error = (await searchParams).error;
  const oidcAvailable = getTelegramOidcConfig() !== null;

  return (
    <main className={styles.page}>
      <section className={styles.panel} aria-labelledby="login-title">
        <div className={styles.brand}>
          <span className={styles.mark} aria-hidden="true">S</span>
          <span>SkyEx Thai CRM</span>
        </div>
        <h1 id="login-title">Вход для сотрудников</h1>
        <p>Используйте Telegram-аккаунт, добавленный администратором CRM.</p>
        {error && <p className={styles.error}>{ERRORS[error] ?? ERRORS.service}</p>}
        <div className={styles.widget}>
          {oidcAvailable ? (
            <a className={styles.loginButton} href="/api/auth/telegram/oidc/start">
              Войти через Telegram
            </a>
          ) : (
            <span className={styles.unavailable}>Вход временно недоступен</span>
          )}
        </div>
      </section>
    </main>
  );
}
