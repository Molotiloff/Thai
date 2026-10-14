# Telegram-вход в Thai CRM

09.10.2026: подключён существующий Telegram OpenID Connect flow российской CRM
к самостоятельным Thai API, справочнику пользователей и cookie `thai_access_token`.
Форма ручного ввода токена удалена; POST `/api/thai-login` отвечает 410.
Команда бота `/token` также отключена; `/вход` выдаёт ссылку на Telegram Login.

## Конфигурация

В backend `.env.thai`:

```dotenv
THAI_TELEGRAM_OIDC_CLIENT_ID=<Client ID тайского бота>
THAI_TELEGRAM_OIDC_CLIENT_SECRET=<Client Secret Telegram Login>
THAI_FRONTEND_ORIGIN=https://bvayy-146-185-179-229.run.pinggy-free.link
```

В frontend `.env.local` либо окружении standalone/systemd нужны только
`THAI_TELEGRAM_OIDC_CLIENT_ID` и `THAI_FRONTEND_ORIGIN`, а также прежний
`THAI_API_BASE_URL=http://127.0.0.1:8001/api/v1`. Client Secret остаётся только
на backend. Standalone необходимо явно передать эти публичные настройки
при запуске: .env.local из исходного проекта не копируется в standalone bundle.
В production можно использовать отдельный frontend EnvironmentFile с этими
публичными переменными; весь backend .env.thai frontend-процессу не передавать.

В BotFather → Login Widget разрешить origin и точный callback:

```text
https://bvayy-146-185-179-229.run.pinggy-free.link
https://bvayy-146-185-179-229.run.pinggy-free.link/api/auth/telegram/oidc/callback
```

При смене бесплатного домена обновить THAI_FRONTEND_ORIGIN в обоих процессах,
Allowed URLs в BotFather и перезапустить backend/frontend. Для OIDC домен уже
нельзя менять только в адресной строке. Client ID/Secret не менять без причины.
Официальная инструкция: https://core.telegram.org/bots/telegram-login

Секрет, добавленный пользователем в .env.thai с именем TELEGRAM_OIDC_CLIENT_SECRET,
перенесён в отдельное THAI_TELEGRAM_OIDC_CLIENT_SECRET; Client ID совпадает с
тайским ботом. Старые российские environment keys runtime не использует.

## Границы и защита

Frontend создаёт state, nonce, PKCE S256 и хранит verifier в HttpOnly cookie
с TTL 10 минут. Callback сравнивает state безопасным сравнением и передаёт code
на Thai API. Общий `api.telegram_oidc.exchange_telegram_oidc_code` проверяет
Telegram JWKS, RS256, issuer, audience, срок и nonce. Повторная реализация
криптографической проверки не добавлялась.

Backend разрешает только сконфигурированный callback, затем находит активного
пользователя в новой Thai БД и проверяет право чтения CRM. Автоматической
регистрации неизвестных Telegram-аккаунтов нет. Выдаётся JWT на 3600 секунд;
callback устанавливает HttpOnly/Secure/SameSite=Lax Thai-cookie и удаляет
временные OIDC-cookie. Ошибки state, configuration, доступа и провайдера
возвращают пользователя на страницу входа без токенов в URL/логах.

09.10.2026: исправлена потеря state при параллельном входе. Ранее повторное
нажатие/вкладка перезаписывали общие state/nonce/verifier, а неудачный callback
очищал их для обеих попыток. Дефект воспроизведён на публичном домене.
Теперь каждая попытка использует отдельную HttpOnly/Secure/SameSite=Lax cookie
`thai_oidc_attempt_<state>` с nonce/verifier и тем же TTL. Callback удаляет только
cookie своей попытки; чужой/неверный state не сбрасывает остальные. Хранятся
не более восьми незавершённых попыток. Старые три cookie поддерживаются для
входа, начатого непосредственно перед обновлением.

Если callback повторился после успешного входа, frontend проверяет JWT через
Thai API `/session` и только при действующей сессии возвращает пользователя
на главную. Наличие cookie само по себе не считается подтверждением входа.
Начало входа с другого host сначала перенаправляется на настроенный
THAI_FRONTEND_ORIGIN, чтобы cookie и callback принадлежали одному домену.
Проверки подписи Telegram, state, nonce, PKCE и прав доступа не отключались.

Регрессионный тест frontend: `pnpm run test:telegram-login` (параллельные
попытки, PKCE/nonce, изолированная очистка при успехе/ошибке, неверный state,
повторный callback, истёкшая сессия, host, лимит cookie, переход со старой версии).
TypeScript, ESLint и production build прошли.
HTTPS-проверка после перезапуска: обе параллельные попытки с заведомо неверным
code доходят до проверки Telegram (ошибка `service`, ожидаемая для тестового
code), вместо прежней ошибки `state`. После первой попытки cookie второй
сохраняется. Реальная авторизация пользователя этим тестом не выполняется.

## Проверки

- Общий unit/Thai PostgreSQL/API/design прогон: **682 passed**.
- TypeScript, ESLint и production build прошли; Ruff без ошибок.
- Новые API-тесты: активный пользователь входит, неактивный/неизвестный
  отклоняется, чужой redirect и неверный PKCE отклоняются, ошибка подписи
  возвращает 401, неполная конфигурация — 503; финансовые операции не создаются.
- Старые tests/unit/test_telegram_oidc.py проверяют подписанный RSA ID token и nonce.
- HTTPS smoke на предоставленном домене: login 200, кнопка Telegram без textarea;
  start 307 на oauth.telegram.org, правильные Client ID/callback/state/nonce/S256,
  HttpOnly/Secure cookie; страница Telegram authorize 200; неверный state
  отклоняется; ручной login 410. Проверка с заведомо недействительным code
  вернула invalid_grant (не invalid_client).
- Браузерная проверка оформления выполнена локально; внешний Pinggy заблокирован
  браузерным клиентом Codex, его HTTP-ответы проверены отдельно. Скриншот:
  `.runtime/telegram-login.jpg`.

Подтверждение входа в собственном Telegram-аккаунте выполняет пользователь.
Реальный успешный callback после подтверждения Telegram в этом прогоне не проходился.


13.10.2026: по ответу пользователя новый origin bvayy записан в backend
`.env.thai` и frontend `.env.local`. OIDC Client ID/Secret сохранены.
Allowed URLs/callback в BotFather нужно обновить под этот origin;
авторизация пользователя и перезапуск frontend этим изменением не выполнялись.
