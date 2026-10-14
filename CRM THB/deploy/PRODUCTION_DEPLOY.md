# Развёртывание SkyEX Thai CRM с нуля

Подготовлено 14.10.2026 для **https://thai-crm.work.gd**.
Инструкция для Ubuntu/Debian с systemd и postgresql-common; команды выполняет
администратор сервера. Это план установки, а не отчёт о выполненном развёртывании.
Чекбоксы отмечать после проверки на сервере.

| Компонент | Новая тайская CRM |
|---|---|
| Домен | `thai-crm.work.gd` |
| Системный пользователь | `skyex-thai` |
| Backend + Telegram worker | `127.0.0.1:8001`, существующий `main.py` |
| Frontend Next.js standalone | `127.0.0.1:3001` |
| PostgreSQL | отдельный кластер `thai`, `127.0.0.1:5433` |
| База / новый пользователь БД | `thai_crm` / `thai_crm_app` |
| Схема / ревизия | `public`, `thai_0022`, таблица `alembic_version_thai` |
| Учёт | THB, себестоимость и прибыль USDT, Asia/Bangkok |

Существующие PostgreSQL на **5432**, базу, пользователя, бота, Nginx vhost и
systemd-сервисы РФ не менять. Не переносить российские клиенты, остатки,
архивы или dump: эта CRM начинает работу с новой базой.

## 1. Проверить сервер и подготовить релиз

- [ ] DNS A-запись `thai-crm.work.gd` указывает на сервер. AAAA оставлять только
  при рабочем IPv6; тогда добавить соответствующие IPv6 listen в vhost.
- [ ] Зафиксированы текущие `pg_lsclusters`, занятые порты и действующие vhost.
  Порты 5433, 8001, 3001 свободны. Если заняты — выяснить владельца и остановить
  установку; чужой кластер не удалять и не переиспользовать.
- [ ] Установлены Python 3.12 с venv, PostgreSQL server/client одной основной
  версии, Nginx и Certbot. Node поддерживает установленный Next.js;
  использовать Node 22/24 и **pnpm 11.3.0** из `packageManager` фронта.
  Проверить `node --version`, `pnpm --version`, `pg_dump --version`.
- [ ] Переданы именно изменённые исходники этой копии backend и папки
  `bookkeeping thai`, а не исходные репозитории российского проекта.
  В архив релиза не включать рабочие `.env`, `.env.thai`, `.env.local`, `.venv`,
  `node_modules`, `.next`, `.runtime`, dump, архивы сообщений и тестовые данные.
  Шаблоны `.env.thai.example` и `frontend.env.example` сохранить.

Команды ниже — от root, если не указано иное. Пример создания каталогов:

```bash
adduser --system --group --home /var/lib/skyex-thai skyex-thai
install -d -o skyex-thai -g skyex-thai -m 0750 /opt/skyex-thai
install -d -o skyex-thai -g skyex-thai -m 0750 /opt/skyex-thai/releases /opt/skyex-thai/frontend-releases
install -d -o skyex-thai -g skyex-thai -m 0700 /opt/skyex-thai/shared /opt/skyex-thai/shared/runtime /var/backups/skyex-thai
pg_lsclusters
ss -ltn
```

Для примеров релиз называется `2026-10-14-01`; при каждой установке выбирать
новое имя. Разместить backend в `/opt/skyex-thai/releases/2026-10-14-01`,
frontend — в `/opt/skyex-thai/frontend-releases/2026-10-14-01`, владельцем
обоих каталогов назначить `skyex-thai`. Не перезаписывать действующий релиз.

## 2. Создать отдельный экземпляр PostgreSQL на 5433

- [ ] Через `pg_lsclusters` определена уже установленная основная версия.
  Ниже `16` — пример: заменить на фактическую версию с серверными бинарниками.
- [ ] Новый кластер называется `thai`; одноимённого кластера ещё нет.
- [ ] Не выполняются `restart postgresql`, изменение порта `main`,
  `pg_dropcluster` и установка нового major PostgreSQL поверх текущего РФ.

```bash
THAI_PG_VERSION=16
pg_createcluster --port=5433 --start-conf=auto -o listen_addresses=127.0.0.1 -o password_encryption=scram-sha-256 "$THAI_PG_VERSION" thai -- --auth-local=peer --auth-host=scram-sha-256
```

Команда создаёт отдельные каталоги данных и конфигурации. Оба параметра
аутентификации задаются явно. В **новом**
`/etc/postgresql/<версия>/thai/pg_hba.conf` оставить:

```text
local   all        postgres                       peer
local   thai_crm   thai_crm_app                    scram-sha-256
host    thai_crm   thai_crm_app   127.0.0.1/32      scram-sha-256
```

Не править `pg_hba.conf` российского кластера. Запустить только новый:

```bash
pg_ctlcluster "$THAI_PG_VERSION" thai start
pg_lsclusters
runuser -u postgres -- psql -X -p 5433 -d postgres -v ON_ERROR_STOP=1
```

В открытом psql создать **нового** владельца. Команда `\password` интерактивно
запросит пароль, не помещая его в текст SQL или историю shell:

```sql
CREATE ROLE thai_crm_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
SET password_encryption = 'scram-sha-256';
\password thai_crm_app
CREATE DATABASE thai_crm OWNER thai_crm_app ENCODING 'UTF8' TEMPLATE template0;
REVOKE CONNECT ON DATABASE thai_crm FROM PUBLIC;
GRANT CONNECT ON DATABASE thai_crm TO thai_crm_app;
ALTER DATABASE thai_crm SET timezone TO 'Asia/Bangkok';
\connect thai_crm
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO thai_crm_app;
\q
```

Роль владеет своей БД и может выполнять миграции без прав суперпользователя
или создания других баз. Не делать `ALTER ROLE postgres PASSWORD ...`.

Проверить подключение новым пользователем; пароль вводится интерактивно:

```bash
psql -X -h 127.0.0.1 -p 5433 -U thai_crm_app -d thai_crm -W -v ON_ERROR_STOP=1
```

```sql
SELECT current_database(), current_user, inet_server_port();
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user;
SELECT count(*) FROM pg_tables WHERE schemaname='public';
SHOW timezone;
\q
```

- [ ] Результат: `thai_crm`, `thai_crm_app`, `5433`; три флага прав — false;
  public-таблиц до миграций — 0; timezone — Asia/Bangkok.
- [ ] `pg_lsclusters` показывает исходный кластер РФ на 5432 в прежнем состоянии.
- [ ] 5433 слушает только loopback, не открыт в firewall наружу.

Основание команд: [Debian pg_createcluster](https://manpages.debian.org/testing/postgresql-common/pg_createcluster.1.en.html),
[PostgreSQL CREATE ROLE](https://www.postgresql.org/docs/18/sql-createrole.html),
[psql](https://www.postgresql.org/docs/current/app-psql.html).

## 3. Настроить конфигурацию и постоянные файлы

- [ ] Создан `/opt/skyex-thai/shared/.env.thai` по `.env.thai.example`,
  владелец `skyex-thai:skyex-thai`, права **0600**.
- [ ] Задан отдельный JWT secret длиной минимум 32 символа и отдельный токен
  тайского бота. Не запускать два polling-процесса с одним токеном.
- [ ] Перенесены именно настройки тайских чатов и администраторов из текущего
  `.env.thai`. Секреты не помещать в документацию, архив релиза или frontend.

Ключевые поля backend (остальные оставить по существующей структуре env):

```dotenv
CRM_DATABASE_SCHEMA=thai
DATABASE_URL=postgresql://thai_crm_app:URL_ENCODED_PASSWORD@127.0.0.1:5433/thai_crm
CRM_JWT_SECRET=REPLACE_WITH_INDEPENDENT_RANDOM_SECRET
CRM_API_HOST=127.0.0.1
CRM_API_PORT=8001
CRM_API_CORS_ORIGINS=
CRM_FRONTEND_ORIGIN=https://thai-crm.work.gd
BOT_TOKEN=REPLACE_WITH_THAI_BOT_TOKEN
ADMIN_IDS=1015722286
REQUEST_CHAT_ID=REPLACE_WITH_THAI_CHAT_ID
ADMIN_CHAT_ID=REPLACE_WITH_THAI_CHAT_ID
BEST_CHANGE_CHAT_ID=REPLACE_WITH_BESTCHANGE_CHAT_ID
DANIYAR_CHAT=REPLACE_WITH_DANIYAR_BALANCE_CHAT_ID
TELEGRAM_OIDC_CLIENT_ID=REPLACE_WITH_CLIENT_ID
TELEGRAM_OIDC_CLIENT_SECRET=REPLACE_WITH_CLIENT_SECRET
MESSAGE_ARCHIVE_ENABLED=true
MESSAGE_ARCHIVE_MEDIA_DIR=.runtime/message_archive/media
MESSAGE_ARCHIVE_TEMP_DIR=.runtime/message_archive/exports
GETBLOCK_REPORTS_DIR=.runtime/aml_reports
```

Это образец, `REPLACE_...` не оставлять как рабочие значения. Пароль в DSN
URL-кодировать, если содержит специальные символы. Значения секретов удобно
генерировать hex-строками. `USDT_WALLET` и реквизиты подключаемых интеграций
заполнить своими значениями до использования соответствующих команд.
Backend читает только выбранный **файл `.env.thai`**. EnvironmentFile, `.env`
российского проекта или `export DATABASE_URL` не заменяют этот файл.

Создать `/opt/skyex-thai/shared/frontend.env` по
`deploy/thai/frontend.env.example`, владелец `skyex-thai`, права 0600:

```dotenv
THAI_API_BASE_URL=http://127.0.0.1:8001/api/v1
THAI_TELEGRAM_OIDC_CLIENT_ID=REPLACE_WITH_SAME_PUBLIC_CLIENT_ID
THAI_FRONTEND_ORIGIN=https://thai-crm.work.gd
```

Next.js выполняет обращения к backend через BFF; API наружу не публикуется.
В frontend.env не должно быть DATABASE_URL, JWT, Client Secret или BOT_TOKEN.
В релизе frontend не оставлять локальный `.env.local` с Pinggy/другим доменом.

- [ ] В настройках Telegram Login/BotFather разрешён точный callback
  **https://thai-crm.work.gd/api/auth/telegram/oidc/callback**.
  Client ID и origin совпадают в обоих файлах; Client Secret — только backend.
- [ ] Тайский бот имеет доступ к заданным служебным чатам, BestChange и Данияру.

От `skyex-thai`, в backend-каталоге релиза создать постоянные ссылки:

```bash
ln -s /opt/skyex-thai/shared/.env.thai .env.thai
ln -s /opt/skyex-thai/shared/runtime .runtime
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 4. Развернуть и проверить итоговую схему

Источники схемы: `db_asyncpg/thai_migrations/versions` и замороженные SQL в
`db_asyncpg/thai_migrations/sql`. Текущая цепочка — **21 миграция**,
`thai_0001` → `thai_0022`. Читаемый итоговый снимок — `db_asyncpg/schema.sql`.
Проверка: [SCHEMA_VERIFICATION.md](thai/SCHEMA_VERIFICATION.md).
Российская цепочка и её снимок удалены. `architecture/thai/schema_draft.sql` —
архитектурный черновик, для установки его не использовать.

Устанавливать **через миграции**, а не импортом schema.sql: снимок не создаёт
таблицу версии Alembic. Не выполнять импорт и затем upgrade/stamp наугад.
Следующие команды не запускают бота и не требуют его токена:

```bash
.venv/bin/python -m db_asyncpg.bootstrap --schema thai --config .env.thai
.venv/bin/python -m db_asyncpg.bootstrap --schema thai --config .env.thai --status
psql -X -h 127.0.0.1 -p 5433 -U thai_crm_app -d thai_crm -W -v ON_ERROR_STOP=1 -f deploy/thai/verify_initial_database.sql
```

- [ ] Status показывает `thai`, `thai_0022`, `thai_crm`, порт **5433**.
- [ ] `verify_initial_database.sql` завершился без ошибки: ноль клиентов,
  денежных операций, вкладов, расходов и остатков; 8 валютных инструментов
  и 8 кассовых счетов, одна безналичная касса.
- [ ] Identity: Thai / USDT / THB / Asia/Bangkok.
- [ ] Повторный bootstrap не меняет ревизию и не дублирует seed.

Нулевое состояние проверять **до первого main.py**. При первом запуске
приложение создаёт администраторов и клиентов заданных BestChange/DANIYAR_CHAT,
привязывает их к учёту BestChange и расходов; после этого клиентов уже не ноль.
Вклады, займы и клиентские кошельки учитываются раздельно. Процент инвестора
хранится в `capital_interest_terms.monthly_interest_percent`, с историей условий;
это не курс валюты и не доля прибыли владельцев.

## 5. Собрать frontend и подготовить systemd

От `skyex-thai` в **frontend-каталоге релиза**:

```bash
set -a
. /opt/skyex-thai/shared/frontend.env
set +a
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run lint
pnpm run test:forms
pnpm run test:telegram-login
pnpm run build
test -f .next/standalone/server.js
```

`build` через существующий prepare-standalone копирует public/static в standalone.
Проверить, что установленный `node` доступен по `/usr/bin/node`; если путь иной,
заменить ExecStart в frontend unit фактическим абсолютным путём.

От root после успешной проверки релизов:

```bash
ln -s /opt/skyex-thai/releases/2026-10-14-01 /opt/skyex-thai/current
ln -s /opt/skyex-thai/frontend-releases/2026-10-14-01 /opt/skyex-thai/frontend-current
install -m 0644 /opt/skyex-thai/current/deploy/thai/skyex-thai-backend.service /etc/systemd/system/skyex-thai-backend.service
install -m 0644 /opt/skyex-thai/current/deploy/thai/skyex-thai-frontend.service /etc/systemd/system/skyex-thai-frontend.service
systemd-analyze verify /etc/systemd/system/skyex-thai-backend.service /etc/systemd/system/skyex-thai-frontend.service
systemctl daemon-reload
```

- [ ] Рабочие каталоги и файлы принадлежат skyex-thai; `.runtime` постоянная.
- [ ] Backend unit запускает существующий **main.py**, без отдельного runtime.
- [ ] Сервисы РФ не изменены. Запуск Thai — только после проверки конфигурации
  и остановки локального экземпляра **этого же тайского** бота.

## 6. Настроить домен, TLS и запустить

Сначала получить сертификат, затем включать итоговый HTTPS vhost. Для нового
домена создать `/var/www/skyex-thai-acme` и **отдельный** временный HTTP vhost
`/etc/nginx/sites-available/skyex-thai`:

```nginx
server {
    listen 80;
    server_name thai-crm.work.gd;
    location ^~ /.well-known/acme-challenge/ { root /var/www/skyex-thai-acme; }
    location / { return 503; }
}
```

От root, если запись sites-enabled ещё не существует:

```bash
install -d -m 0755 /var/www/skyex-thai-acme
ln -s /etc/nginx/sites-available/skyex-thai /etc/nginx/sites-enabled/skyex-thai
nginx -t
systemctl reload nginx
certbot certonly --webroot -w /var/www/skyex-thai-acme -d thai-crm.work.gd
install -m 0644 /opt/skyex-thai/current/deploy/thai/nginx.conf.example /etc/nginx/sites-available/skyex-thai
nginx -t
systemctl reload nginx
systemctl enable --now skyex-thai-backend.service skyex-thai-frontend.service
```

Если сервер использует conf.d вместо sites-enabled, установить только один
аналогичный vhost в его действующую структуру; не подключать конфигурацию дважды.
Не удалять default и российский vhost. Настроить автоматическое продление
сертификата и reload Nginx после успешного продления, проверить `certbot renew --dry-run`.

Итоговый Nginx направляет **все браузерные запросы на 3001**, включая
`/api/thai/*`, OIDC и logout. Проксирование всего `/api/` на 8001 ломает формы
и авторизацию. Backend слушает loopback, Next обращается к нему серверным запросом.

## 7. Приёмка после запуска

```bash
systemctl status skyex-thai-backend skyex-thai-frontend
journalctl -u skyex-thai-backend -u skyex-thai-frontend --since today
curl --noproxy '*' --fail http://127.0.0.1:8001/health/live
curl --noproxy '*' --fail http://127.0.0.1:8001/health/ready
curl --noproxy '*' --fail https://thai-crm.work.gd/login
```

От skyex-thai в backend current:

```bash
.venv/bin/python -m app.thai_maintenance --config .env.thai check --frontend-env /opt/skyex-thai/shared/frontend.env --frontend-url http://127.0.0.1:3001 --output .runtime/production-readiness.json
```

Команда не исправляет данные и не отправляет сообщения. Код 0 — нет fail,
2 — остались незавершённые проверки. `--expect-empty` после создания служебных
клиентов уже неприменим. JSON-отчёт не публиковать через Nginx.

- [ ] Health ready успешен, нет ошибок миграций и конфликта Telegram polling.
- [ ] Telegram-вход на домене работает в Chrome, повторный вход и выход работают.
- [ ] Администратор видит нужные разделы; права менеджера проверены отдельно.
- [ ] Клиенты BestChange и Данияра автоматически связаны из env, ручной плашки нет.
- [ ] Перед реальными деньгами на отдельном тестовом контуре проверены /start,
  /кошелек, /депбат, /выдбат, покупка/продажа/конвертация и заявки из CRM:
  номер, автоподсчёт, сообщение клиенту, фактический расчёт после обеих сторон.
- [ ] Проверены вклад/процент инвестора, FIFO возврат в исходной валюте,
  отрицательный кошелёк, заём группы Партнер, доли расходов и месячная выписка,
  BestChange и перенос Данияру, прибыль/убыток USDT, сторно и ревизии.
- [ ] Проверены архив сообщений, права на постоянные media-каталоги и очередь outbox.
- [ ] РФ остаётся доступен, PostgreSQL 5432 не менялся.

## 8. Backup, обновления и восстановление

От skyex-thai в backend current перед обновлением, с новым именем каждого backup:

```bash
.venv/bin/python -m app.thai_maintenance --config .env.thai backup --output /var/backups/skyex-thai/2026-10-14-before-update.dump
```

Хранить dump вместе с `.dump.json` (checksum/revision), права 0600, дополнительно
копировать вне сервера в закрытое хранилище. Отдельно резервировать защищённый
shared env и media/runtime; PostgreSQL dump не содержит файлов архива.
Назначить регулярное расписание, срок хранения и ответственного.

Контроль восстановления: DBA на **5433** создаёт отдельную пустую базу
`thai_restore_check` с владельцем `thai_crm_app`; добавляет только для неё
аналогичные строки scram в HBA нового кластера и делает reload **этого кластера**.
Приложению NOCREATEDB повышать права не нужно. Затем:

```bash
.venv/bin/python -m app.thai_maintenance --config .env.thai restore --backup /var/backups/skyex-thai/2026-10-14-before-update.dump --database thai_restore_check
```

Проверить identity, ревизию, журнал, кассу/себестоимость, кошельки, капитал,
начисления, выписки, закрытия и outbox на копии. Не запускать её бота с рабочим
токеном. Restore выполняется только в новую базу, не поверх действующей.

Порядок обновления:

1. Проверить новый релиз и миграции на изолированной копии, записать версии.
2. Остановить только skyex-thai-backend и skyex-thai-frontend, сделать backup.
3. Подготовить новые каталоги и ссылки env/runtime, собрать frontend.
4. Выполнить bootstrap Thai и status новым backend.
5. Переключить current/frontend-current на новый релиз, запустить только Thai.
6. Повторить health, вход, права и проверку очереди; сохранить предыдущий релиз.

При ошибке вернуть приложение на предыдущий релиз только при совместимости
с новой схемой; иначе исправлять вперёд. Финансовую БД автоматически не
понижать и не удалять проводки. При аварийном восстановлении сначала остановить
Thai, сверить операции после backup и уже доставленные Telegram-сообщения:
старый outbox может повторить доставку. Исходную БД сохранить для сверки.

- [ ] Backup создаётся и проверенное восстановление выполнено.
- [ ] Записаны фактические пути релизов, версия PostgreSQL, домен и ревизия.
- [ ] Ответственный за учёт принял контрольные сценарии перед рабочими операциями.
