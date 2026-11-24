# SkyEX Thai CRM — исходная точка этапа 0

25 ноября 2026 года. Коммит `306bb70037252059b5d498b7165f22b9c01b463d`.
Исходники backend и frontend совпадают с этим коммитом. Работа выполнена по
[промту этапа 0](REFACTORING_EXECUTION_PLAN.md#этап-0-зафиксировать-границы-и-исходные-проверки),
с опорой на [предыдущий аудит](PROJECT_STATUS_AND_REFACTORING.md). Выводы
относятся к коду и локальной сборке. Конфигурацию и трафик рабочего сервера
на этом этапе не проверяли.

## Что подтвердилось по аудиту

| Граф | Точки входа | Результат |
| --- | --- | --- |
| Python | `main`, `api.thai_server`, `db_asyncpg.bootstrap`, `app.thai_maintenance` | 448 модулей в графе, 306 достижимы. 112 вне графа и вне пакета миграций. Из них 40 в `api`, 32 в `db_asyncpg`, 27 в `services`, 8 в `telegram_adapters`, 4 в `handlers`, 1 в `middlewares`. Циклов нет. |
| Frontend | 29 файлов Next.js с зарезервированными именами в `src/app` и `src/proxy.ts` | Из 258 TS/TSX/CSS-файлов 140 достижимы, 118 вне графа. Это 62 файла TS/TSX и 56 CSS. |

Python проверен существующим `architecture_checks.import_graph` с обходом
импортов от указанных корней, включая родительские пакеты. Для frontend
импорты и реэкспорты разрешались TypeScript по `tsconfig.json`, плюс импорты
CSS и CSS `@import`. Учтены импорты типов. Ручные CLI, динамические загрузки,
сохранённые ссылки и внешние потребители сверяются отдельно. Эти числа
описывают границы инвентаризации и сами по себе не разрешают удаление.

## Рабочие точки входа

### Backend HTTP

Реестр получен из `app.routes` действующей [Thai-фабрики](CRM%C2%A0THB/api/thai_finance.py),
которую вызывает [create_api_app](CRM%C2%A0THB/api/app.py). Контейнер собран
на заглушке пула с тестовым секретом. При сборке схемы соединение с БД и
lifespan не запускались. Итог — **87 путей и 97 пар метод + путь в OpenAPI**.
В реестре нет WebSocket, в том числе `/ws/deals`. Маршруты
`api/routers/*` и `api/ws.py` не подключены этой фабрикой.

Маршруты вне OpenAPI:

| Методы | Путь | Назначение |
| --- | --- | --- |
| GET | `/health/live` | Проверка процесса |
| GET | `/health/ready` | Проверка доступности и ревизии изолированной Thai-схемы |
| GET, HEAD | `/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc` | Служебные маршруты FastAPI |

Полный реестр под `/api/v1`. Методы в одной строке относятся к одному пути.

```text
POST       /api/v1/auth/telegram/oidc
POST, GET  /api/v1/best-change/bindings
POST, GET  /api/v1/best-change/deals
POST       /api/v1/best-change/deals/{deal_id}/correct
POST       /api/v1/best-change/preview
GET        /api/v1/best-change/report
GET, POST  /api/v1/capital/agreements
GET        /api/v1/capital/agreements/{agreement_id}
POST       /api/v1/capital/agreements/{agreement_id}/accruals
POST       /api/v1/capital/agreements/{agreement_id}/contributions
GET        /api/v1/capital/agreements/{agreement_id}/history
POST       /api/v1/capital/agreements/{agreement_id}/interest-payments
POST       /api/v1/capital/agreements/{agreement_id}/operations/{operation_id}/reversal
POST       /api/v1/capital/agreements/{agreement_id}/terms
POST       /api/v1/capital/agreements/{agreement_id}/withdrawals
GET        /api/v1/capital/investors
GET        /api/v1/capital/investors/{investor_id}/history
POST       /api/v1/cash-requests
PUT        /api/v1/cash-requests/{deal_id}
POST       /api/v1/cash-requests/{deal_id}/settle
GET, POST  /api/v1/client-groups
PUT        /api/v1/client-groups/{code}/name
GET, POST  /api/v1/clients
POST       /api/v1/clients/transfers
GET        /api/v1/clients/transfers/{operation_id}
POST       /api/v1/clients/transfers/{operation_id}/adjust
GET        /api/v1/clients/workspace
GET        /api/v1/clients/{client_id}
POST       /api/v1/clients/{client_id}/chat
POST       /api/v1/clients/{client_id}/close
PUT        /api/v1/clients/{client_id}/group
GET        /api/v1/clients/{client_id}/statement
PUT        /api/v1/clients/{client_id}/telegram-link
GET        /api/v1/clients/{client_id}/wallets
POST       /api/v1/clients/{client_id}/wallets/{instrument}/adjustments
POST       /api/v1/clients/{client_id}/wallets/{instrument}/deposits
GET        /api/v1/clients/{client_id}/wallets/{instrument}/history
POST       /api/v1/clients/{client_id}/wallets/{instrument}/withdrawals
POST       /api/v1/clients/{client_id}/zero
POST       /api/v1/clients/{client_id}/zero-preview
GET, POST  /api/v1/deals
GET        /api/v1/deals/counterparties
POST       /api/v1/deals/profit
GET        /api/v1/deals/workspace
GET        /api/v1/deals/{deal_id}
POST       /api/v1/deals/{deal_id}/cancel
POST       /api/v1/deals/{deal_id}/correction
POST       /api/v1/deals/{deal_id}/payment-watch
POST       /api/v1/deals/{deal_id}/post
POST       /api/v1/deals/{deal_id}/profit-cancel
POST       /api/v1/deals/{deal_id}/profit-correction
POST       /api/v1/deals/{deal_id}/ready
GET        /api/v1/expense-categories
GET        /api/v1/expense-debts
POST       /api/v1/expense-operations/{operation_id}/reversal
GET        /api/v1/expense-participants
POST       /api/v1/expense-periods/close
POST, GET  /api/v1/expense-periods/revisions
POST       /api/v1/expense-settlements
GET        /api/v1/expense-settlements/{resolution_id}
GET        /api/v1/expense-statements
GET, POST  /api/v1/expenses
POST       /api/v1/expenses/participants/{code}/binding
GET        /api/v1/expenses/{expense_id}
GET        /api/v1/finance/cash
POST       /api/v1/finance/cash-transfers
GET        /api/v1/finance/operations
GET        /api/v1/finance/operations/{operation_id}
POST       /api/v1/finance/operations/{operation_id}/reversal
POST       /api/v1/finance/projections/rebuild
GET        /api/v1/instruments
GET        /api/v1/loans
GET        /api/v1/loans/totals
GET        /api/v1/loans/{client_id}/wallets/{instrument}/history
GET        /api/v1/payment-watches/{watch_id}
POST       /api/v1/profit/close
GET        /api/v1/profit/closures
GET        /api/v1/profit/obligations
POST       /api/v1/profit/operations/{operation_id}/reversal
POST       /api/v1/profit/payments
GET        /api/v1/profit/report
POST, GET  /api/v1/profit/revisions
GET, POST  /api/v1/profit/terms
GET        /api/v1/reports/dashboard
GET        /api/v1/reports/operations
GET        /api/v1/session
POST       /api/v1/wallet-transfers
```

### Frontend и BFF

Пути сверены с manifest после standalone-сборки. Обычные страницы: `/`,
`/login`, `/clients`, `/clients/[id]`, `/deals`, `/deals/new`,
`/deals/[id]`, `/turnover`, `/turnover/[id]`, `/loans`, `/expenses`,
`/expenses/[id]`, `/profit`, `/best-change`, `/operations` и
`/operations/[id]`. Метаданные и внутренние входы Next.js — `/favicon.ico`,
`/_not-found` и `/_global-error`.

Редиректы `/accounting → /profit`, `/attendance → /` и `/balances → /loans`
сохраняют старые ссылки. Ссылка `/deals/new?type=best_change` переводит на
`/best-change`. При очистке стилей и компонентов эти входы следует сохранить.

| Методы в исходнике | Путь | Назначение |
| --- | --- | --- |
| GET | `/api/auth/telegram/oidc/start` | Начало входа |
| GET | `/api/auth/telegram/oidc/callback` | Завершение входа |
| POST | `/api/thai-login` | Ответ 410 для прежнего входа |
| POST | `/api/thai-logout` | Завершение сессии |
| GET, POST, PUT | `/api/thai/[...path]` | Прокси в Thai API с проверкой origin, сессии и допустимого корня |

`src/proxy.ts` пропускает публичные пути входа и проверяет наличие cookie для
остальных страниц/BFF. Права на изменение проверяет backend. Шаблон nginx
направляет браузерные запросы в Next.js на `3001`. Внутренний API указан на
`8001`. [Хуки маршрутизации](bookkeeping%20thai/src/proxy.ts) ·
[Шаблон nginx](CRM%C2%A0THB/deploy/thai/nginx.conf.example)

### Telegram

Регистрация сверена через [setup_handlers](CRM%C2%A0THB/app/setup_handlers.py)
с конфигурацией архива on/off, заглушкой пула и тестовым объектом бота без
polling. Учитывались явные регистрации и общий обработчик `/...`.
В нём команды приводятся к нижнему регистру и разбирается суффикс `@bot`.
Список фиксирует входы. Проверки прав и условия бизнеса остаются за
конкретными обработчиками.

| Группа | Команды и алиасы в Thai runtime |
| --- | --- |
| Начало, справка и вход | `/start`, `/помоги`, `/help`, `/помощь`, `/команды`, `/info`, `/кнопки`, `/скрыть`, `/вход`; `/token` отвечает, что вход теперь через Telegram |
| Кошельки и клиенты | `/кош`, `/usdt` без аргументов, `/setwallet`, `/setкош`, `/кошелек`, `/кошелёк`, `/дай`, `/выписка`, `/добавь`, `/удали`, `/обнулить`, `/закрыть`, `/клиенты`, `/регистрация`, `/привязать`, `/группа`, `/бк` |
| Обмен в кошельке | Приём `/пбат`, `/пбезнал`, `/пд`, `/пб`, `/пт`, `/пе`, `/пп`, `/пр`. Вторая сторона задаётся аргументами `обат`, `обезнал`, `од`, `об`, `от`, `ое`, `оп`, `ор`. Поддерживаются ответ для исправления и отмена. |
| Заявки кассы | 16 команд `/деп{суффикс}` и `/выд{суффикс}`, 56 команд `/п{вход}в{выход}` для разных инструментов. Суффиксы `бат, безнал, д, б, т, е, п, р`. Подготовка и проведение — `/готово`, `/провести`, `/<валюта> сумма Б-…`. |
| Корректировки и переводы | `/<валюта> сумма` для корректировки кошелька или общей кассы по контексту чата. `/перевод` для создания и изменения ответом. `/отмена` сначала проверяет ответ на перевод, затем на обмен, а по ID работает с документом. `/сторно` принимает ID операции и причину. |
| Мониторинг и отчёты | `/отпр` и вариант с аргументом `тест`. `/бест` с `покупка/купить`, `продажа/продать`, `отчет/отчёт/баланс`, `история`, `исправить`, `сторно`, `привязать`, `закрыть`. `/прибыль` для учёта прибыли/убытка, также `закрыть` и `выплата`. |
| Служебные и условные | `/whoami`, `/xe` (провайдер по конфигурации), `/mgr`, `/всем` и ответы на сообщение рассылки, `/амл` (GetBlock настроен), `/акт` (рабочий/админский чат), `/calc`, строки под regex `^/[+\-0-9(]` и inline-калькулятор. `/сообщение` ищет и экспортирует архив, при отключении сообщает о недоступности. |

22 валютных алиаса для `/<валюта>` и валютных аргументов: `бат, thb, нал,
thb_cash, безнал, thb_bank, usd, дол, доллар, usd_bl, usdw, долб, долбел,
usd_wh, usdt, юсдт, eur, евро, eur500, евро500, rub, руб`.
[Парсер и словарь](CRM%C2%A0THB/app/thai_russian_commands.py) ·
[Команды кассы](CRM%C2%A0THB/services/cash_requests/constants.py)

| Callback | Путь выполнения |
| --- | --- |
| `cash:deal_ready:req:`, `cash:deal_done:req:`, `cash:deal_cancel:req:` | Статус кассовой заявки |
| `thai:cash:confirm` (точное значение) | Ответ на старый формат подтверждения |
| `req_cancel:` | Отмена заявки обмена |
| `paywatch:` | Управление ожиданием, с проверкой исходной карточки |
| `ct:`, `stmt:`, `undo:`, `zero:` | Переводы, выписки, отмена корректировок и обнуление |
| `broadcast:confirm`, `broadcast:cancel` (точные значения) | Подтверждение и отмена рассылки |
| `message_archive:chat:` | Выбор чата для выгрузки. Регистрируется при включённом архиве. |

Родительский роутер Thai регистрирует 8 обработчиков callback. Ещё один
регистрирует рассылка, ещё один — архив при включении. Тестовая сборка
подтвердила `message`, `callback_query`, `inline_query` как используемые
базовые обновления. Перед polling при включённом архиве `BotApp` добавляет
`edited_message`, `channel_post`, `edited_channel_post`,
`business_message` и `edited_business_message`. Регистрации в старых
конструкторах обработчиков без подключения их роутера не входят в этот список.
[Запуск бота](CRM%C2%A0THB/bot_app.py)

Фоновые задачи, которые собирает текущий бот:

- `PaymentMonitor` с `TronscanGateway` и worker финансового Telegram outbox.
- Очередь AML при настроенном GetBlock.
- Lifecycle архива при `MESSAGE_ARCHIVE_ENABLED`: медиа, задачи экспорта и
  мониторинг. Значение по умолчанию в коде — `true`. Фактическое наполнение,
  охват и доступ к экспорту на сервере относятся к этапу 3.
- Поля ежедневных балансов, месячного BestChange, капитализации, Rapira,
  orderbook и backfill ссылок присутствуют в `BotRuntimeServices`, но
  `setup_handlers` их не заполняет. Перед очисткой на этапе 4 проверить
  внешнее расписание и сохранить нужные тесты совместимости.
  [Lifecycle](CRM%C2%A0THB/app/runtime.py)

### Запуск, обслуживание и ручные инструменты

| Точка входа | Назначение и граница |
| --- | --- |
| `.venv/bin/python main.py` | systemd backend. Проверка Thai-схемы, миграции, API при включённом флаге и polling при настроенном боте. |
| `.venv/bin/python -m api.thai_server` | Запуск API без Telegram polling и его фоновых задач. |
| `pnpm build` → `.next/standalone/server.js` | systemd frontend. Для разработки есть `pnpm dev`. |
| `python -m db_asyncpg.bootstrap --schema thai --config .env.thai [--status]` | Bootstrap миграций или чтение текущей ревизии в режиме `--status`. Установка из `schema.sql` не используется при выкладке. |
| `python -m db_asyncpg.local_thai` | Ручная подготовка отдельного локального кластера и env. Отказывается перезаписывать существующие данные и конфиг. |
| `python -m app.thai_maintenance --config .env.thai check / backup / restore` | При запуске выбирается одна из трёх подкоманд. Restore предназначен для новой изолированной базы. |
| `scripts/import_telegram_history.py` | Ручной Telegram Desktop импорт с `--dry-run` и `--apply`. Нужен сценарию архива, даже если скрипт отсутствует в графе запуска. |
| `scripts/benchmark_message_archive_export.py` | Измерение экспорта архива вне процесса бота. |
| `scripts/import_accounting_snapshot.py` | Ручной прежний импорт учёта. В режиме записи читает общий `DATABASE_URL` и использует прежний UoW. Не включать в процедуру Thai без отдельной сверки назначения и владельца. |
| `scripts/repairs/2026-09-22_tajo_payment_watch_shift.sql` | Разовая ремонтная запись. Не часть штатного запуска или цепочки Alembic. |
| `deploy/thai/verify_initial_database.sql` | Проверка новой пустой рабочей базы после bootstrap. Сохраняется как отдельный инструмент эксплуатации. |

Проверены файлы из Git. Локальные, игнорируемые скрипты не входят в состав
релиза из `git archive`. [Развёртывание](CRM%C2%A0THB/deploy/PRODUCTION_DEPLOY.md)
· [CLI обслуживания](CRM%C2%A0THB/app/thai_maintenance.py)

## Первая партия для этапа 1

Перед удалением сначала перевести **две рабочие страницы клиентов** с
`@/services` на прямую сборку существующего `ClientsService`.
`/clients` и `/clients/[id]` используют его чтение `/clients/workspace`.
Редактирование ссылки в актуальном `ClientInfoPanel` уже идёт через
`PUT /api/thai/clients/{client_id}/telegram-link`. При переносе сохранить
этот путь, права и замену ссылки в интерфейсе. Моки, `ApiClient` и весь
`src/components` в первую партию не включать.
[Сборка сервисов](bookkeeping%20thai/src/services/index.ts) ·
[Панель клиента](bookkeeping%20thai/src/components/clients/ClientInfoPanel.tsx)

| Кандидат, относительно `bookkeeping thai/src/` | Строк | Основание и проверка |
| --- | ---: | --- |
| `components/main/ManualCashPanel.tsx` | 66 | Входящих импортов нет. Рабочая главная рендерит Thai Dashboard с `CurrenciesPanel` и `FinancePanel`. Сверить главную и «Журнал» после удаления. |
| `components/main/ManualCashPanel.module.css` | 9 | Единственный входящий импорт из удаляемой панели. Проверка — сборка и главная. |
| `app/manualCashActions.ts` | 60 | Обе server actions вызываются только из этой панели. `actions.ts` здесь не самостоятельный маршрут Next.js. Проверить отсутствие ссылок в сборке и действующие формы операций. |
| `app/accounting/page.module.css` | 32 | Импортов нет. Соседний `page.tsx` делает редирект. Сохранить и проверить редирект на `/profit`. |
| `app/attendance/page.module.css` | 22 | Импортов нет. Соседний `page.tsx` делает редирект. Сохранить и проверить редирект на `/`. |

**Итого — 5 файлов, 189 строк.** Первые три образуют одну группу, два CSS
можно удалить отдельным коммитом. Проверены разрешённые импорты, поиск ссылок
по Git и manifest сборки. После смены импорта клиентов проверить
страницы клиентов, редиректы, главную и тесты форм. Отдельный поиск перед
удалением нужен, если целевой коммит уже изменился.

### Что пока отложено

| Группа | Условие следующего шага |
| --- | --- |
| `DealsRealtimeRefresh.tsx` (84 строки) и `/ws/deals` | У компонента нет импортов, у Thai API нет регистрации WebSocket. Старые workflow и тесты упоминают путь. Для совместной очистки с backend сверить фактический Thai vhost и договорённости с внешними клиентами. Логи российского vhost не считаются трафиком Thai. |
| `src/services/index.ts`, сервисы и моки | Из рабочих страниц остаются два входа через клиентов. Сначала перенести их и заново построить граф, затем удалять связанный контур. |
| `app/clients/actions.ts` | Входящих импортов нет, актуальная форма идёт через BFF. Оценить после перевода страниц, когда будет понятна оставшаяся граница server actions. |
| Старые Python-роутеры, DTO и тесты | Этап 2. Перед удалением проверить внешний запуск приложения и перенести актуальные проверки прав, ошибок и форматов на Thai API. |
| Архив, резервное копирование, миграции и CLI | У них другие точки входа. Хранить до соответствующего этапа и сверки сценариев. |

Для внешней сверки нужны конфигурация именно Thai vhost, параметры запуска
двух Thai-сервисов, информация о таймерах/cron и список известных внешних
потребителей. Nginx access logs могут подтвердить обращения к старому пути
по методу, пути и статусу. В отчёт достаточно сводных счётчиков, без заголовков
авторизации, query-строк и пользовательских данных. Отсутствие строки в одном
журнале не подтверждает отсутствие потребителя. Эта сверка нужна для
перечисленных отложенных удалений, а не для завершения локальной инвентаризации.

## Базовый прогон

| Проверка | Результат |
| --- | --- |
| Backend Ruff и граф импортов | Прошли. Циклов нет в 448 модулях. |
| Backend `pytest tests/unit tests/thai tests/design tests/message_archive` | **1 141 тест прошёл** за 190,31 с. Одно предупреждение Starlette о совместимости TestClient с httpx. |
| Frontend `typecheck`, ESLint с `--max-warnings 0`, `test:forms`, `test:telegram-login`, standalone build | Прошли. Доступные локально Node 25.1.0 и pnpm 11.3.0. Использовано существующее `node_modules`; CI закрепляет Node 22 и отдельно проверяет установку по lockfile. |

Backend проверен на Python 3.12.3 и чистой временной PostgreSQL 16.14 на
localhost, порт 55446, с тестовой ролью. `pg_dump` и `pg_restore` тоже
версии 16.14. Прогон не подтверждает совместимость с Python 3.14 / PostgreSQL
18 на сервере и не заменяет удалённый CI. [Конфигурация CI](.github/workflows/ci.yml)
