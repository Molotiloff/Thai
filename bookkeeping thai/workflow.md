# Workflow фронтенда SkyEx CRM

Документ синхронизирован с `workflow_crm.md`. Текущий фронтенд - черновик CRM на Next.js, который должен стать веб-интерфейсом к уже существующему денежному ядру Telegram-бота, а не отдельной параллельной системой учета.

Главное правило: фронт не придумывает бизнес-логику. Источником истины являются backend use-cases бота/CRM API и Postgres. Фронт отображает DTO API, отправляет команды и подписывается на realtime-события.

## 1. Что уже реализовано на стороне бота/бэка

По `workflow_crm.md` уже есть Telegram-бот на aiogram 3 + asyncpg + Postgres с боевыми денежными механизмами:

- Клиенты и кошельки: `clients`, `client_accounts`, `ClientsRepo`, `WalletService`.
- Immutable-журнал денег: `transactions`, атомарные `deposit/withdraw`, `balance_after`, idempotency key.
- Обменные заявки: `services/exchange`, `exchange_request_links`, создание/отмена/редактирование, компенсационные дельты.
- Наличные заявки: `services/cash_requests`, выдача, завершение, отмена, расписание выдач.
- Сверка активных заявок: `act_request_transactions`, `services/act_counter`.
- Ожидание оплаты и Tronscan: `payment_watches`, `payment_watch_events`.
- Балансы клиентов: `services/client_balances`, nonzero balances, фильтрация, daily reports.
- Автосоздание клиента при добавлении бота в чат.
- Номера заявок: `request_id_seq`.
- Формулы в суммах: `utils/calc`.
- Ордера по курсу: `services/rate_order`, `rate_orders`.
- Telegram-карточки заявок и text builders для обновлений.
- Google Sheets как переходная витрина и ручной учет.

Это значит, что текущие mock-сервисы фронта нужно постепенно заменить на API-адаптеры, которые читают и вызывают существующие use-cases через FastAPI.

## 2. Целевая связка фронта и бэка

Целевой flow:

```text
Telegram Bot -> services/use-cases -> Postgres <- FastAPI CRM API <- Next.js Frontend
                         |
                         +-> tg_outbox -> Telegram card updates
```

Фронт работает только через:

- REST `/api/v1/*` для данных и команд.
- WebSocket `/ws/deals` для realtime-доски сделок.
- Telegram Login Widget -> JWT-сессия.
- RBAC API + route-level checks во фронте.

Важно: `workflow_crm.md` предлагает React + Vite как целевой frontend. Фактическая реализация в этом репозитории уже на Next.js 16 App Router. Чтобы не плодить второй фронт, практичный путь - продолжить текущий Next.js, но привести его к API-first архитектуре. Если команда принципиально решит перейти на Vite, этот документ можно использовать как функциональную карту миграции компонентов.

## 3. Текущая карта страниц фронта

| Маршрут | Текущее состояние | Backend-источник по целевой схеме | Что привести в соответствие |
| --- | --- | --- | --- |
| `/` | Mock dashboard: KPI, валюты, финансы, города | `GET /api/v1/dashboard`, `GET /api/v1/dashboard/rates`, позже `cash_desks`, `app_settings` | Разделить данные для manager+ и accountant+; курсы на переходе читать из Sheets/app_settings через API |
| `/deals` | Mock kanban + таблица сделок | `GET /api/v1/deals`, `WS /ws/deals` | Сменить статусы на backend-модель: `new`, `fixed`, `balance_check`, `awaiting_payment`, `in_delivery`, `done`, `canceled` |
| `/deals/new` | Форма 10 типов с локальными расчетами | `GET /api/v1/deals/schema`, `POST /api/v1/deals`, backend `deal_calc.py` | Убрать бизнес-расчеты с фронта, оставить preview только из API/schema |
| `/clients` | Mock список, карточка, комментарии | `GET /api/v1/clients`, `GET /clients/{id}`, comments endpoints | Подключить реальные клиенты, chat_id, балансы, историю транзакций, прибыль по сделкам |
| `/balances` | Mock ненулевые балансы | `GET /api/v1/balances?currency=&sign=` | Использовать `client_balances/nonzero_wallet_query_service`, убрать дубли типа валют |
| `/accounting` | Mock кассы, перемещения, P&L, графики | `GET /api/v1/accounting/desks`, `/moves`, `/pnl`, `/report.pdf` | Отделить кассы фирмы от балансов клиентов; учитывать RBAC accountant+ |
| `/expenses` | Mock расходы, вкладка отчетов-заглушка | `GET/POST/PATCH/DELETE /api/v1/expenses`, `/expenses/summary` | Сделать CRUD расходов, pie chart, PDF-share/report |
| `/attendance` | Mock календарь | `GET/PUT /api/v1/attendance?month=` | Подключить пользователей CRM и реальные переработки |
| `/turnover` | Mock оборот, владельцы, вклады/выводы | `GET/POST /api/v1/turnover` | Ограничить owner/admin, связать с `capital_moves` |

Пункты меню без страниц: `/cash`, `/income`, `/reports`, `/settings`. До реализации их нужно либо скрыть по feature flags, либо добавить страницы-заглушки с корректными RBAC-проверками.

## 4. Главные расхождения текущего фронта с backend workflow

### 4.1 Статусы сделок

Сейчас фронт использует:

- `fixed`
- `awaiting_payment`
- `balance_check`
- `completed`
- `insufficient_usdt`

Backend workflow задает:

- `new`
- `fixed`
- `balance_check`
- `awaiting_payment`
- `in_delivery`
- `done`
- `canceled`

Рекомендация: `insufficient_usdt` не должен быть статусом сделки. Это derived flag/alert на колонке `balance_check`, который считается по `act_request_transactions` и правилу недостатка Теза. `completed` заменить на `done`.

### 4.2 Типы сделок

Сейчас фронт:

- `sale`
- `purchase`
- `cash_in`
- `cash_out`
- `delivery`
- `rearrangement`
- `conversion`
- `yuan`
- `invoice`
- `profit`

Backend workflow:

- `sale`
- `purchase`
- `deposit`
- `withdrawal`
- `delivery`
- `transfer_city`
- `conversion`
- `yuan`
- `invoice`
- `profit`

Рекомендация: переименовать фронтовые типы:

- `cash_in` -> `deposit`
- `cash_out` -> `withdrawal`
- `rearrangement` -> `transfer_city`

Названия должны совпадать с Pydantic-схемами и `deals.deal_type`.

### 4.3 Расчеты формы сделки

Сейчас `src/components/deals/new/newDealConfig.ts` одновременно содержит UI-config, default values, validation, parsing и business calculation.

По backend workflow расчеты должны жить в `services/crm/deal_calc.py`, а фронт должен получать схему через `GET /api/v1/deals/schema`.

Рекомендация:

- На фронте оставить только rendering динамических полей.
- Для preview расчетов вызывать API `POST /api/v1/deals/preview` или использовать формулы, возвращенные schema endpoint, если backend это поддержит.
- Submit всегда отправляет typed payload в `POST /api/v1/deals`.

### 4.4 Клиенты и балансы

Сейчас фронт дублирует клиента как `Client`, `BalanceClient`, строковые поля сделки и `NewDealContext.clients`.

Backend уже имеет клиентов, кошельки, историю транзакций и автосоздание клиента из Telegram. Фронт должен использовать backend DTO:

- `ClientListItem`
- `ClientDetails`
- `ClientBalance`
- `TransactionHistoryItem`
- `ClientComment`

### 4.5 Бухгалтерия и балансы

Важно не смешивать:

- Балансы клиентов: `client_accounts`, `transactions`.
- Кассы фирмы: будущие `cash_desks`, `cash_desk_moves`.

Текущий `/balances` - клиентские балансы. `/accounting` и будущий `/cash` - кассы фирмы.

### 4.6 Роли

В текущем фронте роли только фильтруют sidebar. По workflow CRM RBAC должен быть и в API, и во фронтовом routing.

Матрица ролей:

- `cashier`: сделки своих городов, чтение клиентов/балансов.
- `manager`: сделки, клиенты, балансы, внесение расходов.
- `accountant`: расходы, бухгалтерия, P&L.
- `owner`: бухгалтерия, оборот владельцев.
- `admin`: все, включая пользователей/настройки.

Фронт не должен показывать отчеты и статистику кассиру/обычному менеджеру.

## 5. Целевая фронтенд-архитектура текущего Next.js приложения

```text
src/
  app/                         маршруты Next.js
  api/                         HTTP/WS clients, auth session, generated API types
  domain/                      shared frontend domain models aligned with API DTO
    money/
    clients/
    deals/
    users/
    permissions/
  application/                 thin client-side use cases/view models
  components/
    ui/                        reusable UI
    layout/
    <feature>/                 feature presentation
  hooks/                       pagination, filters, websocket, permissions
  services/                    temporary facade; mock/api implementations
```

На время миграции можно сохранить текущий `src/services` как фасад:

- `MockDealsService` для локальной разработки без API.
- `ApiDealsService` для реального backend.
- `services/index.ts` выбирает реализацию по env (`NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_USE_MOCKS`).

После стабилизации API лучше заменить ручные интерфейсы на типы из OpenAPI (`openapi-typescript`) и тонкие API clients.

## 6. API endpoints, которые должен ожидать фронт

Минимальный контракт из `workflow_crm.md`:

```text
POST   /api/v1/auth/telegram
GET    /api/v1/me

GET    /api/v1/dashboard
GET    /api/v1/dashboard/rates

GET    /api/v1/deals?status=&city=&type=&client_id=&period=
POST   /api/v1/deals
GET    /api/v1/deals/schema
GET    /api/v1/deals/{id}
PATCH  /api/v1/deals/{id}
POST   /api/v1/deals/{id}/status
WS     /ws/deals

GET    /api/v1/clients?search=&group=
POST   /api/v1/clients
GET    /api/v1/clients/{id}
PATCH  /api/v1/clients/{id}
POST   /api/v1/clients/{id}/comments
DELETE /api/v1/clients/comments/{cid}

GET    /api/v1/balances?currency=&sign=

GET    /api/v1/expenses
POST   /api/v1/expenses
PATCH  /api/v1/expenses/{id}
DELETE /api/v1/expenses/{id}
GET    /api/v1/expenses/summary?period=

GET    /api/v1/accounting/desks
POST   /api/v1/accounting/moves
GET    /api/v1/accounting/pnl?month=
GET    /api/v1/accounting/report.pdf

GET    /api/v1/turnover
POST   /api/v1/turnover

GET    /api/v1/attendance?month=
PUT    /api/v1/attendance?month=

GET    /api/v1/admin/users
POST   /api/v1/admin/users
PATCH  /api/v1/admin/users/{id}
GET    /api/v1/admin/counterparties
POST   /api/v1/admin/counterparties
PATCH  /api/v1/admin/counterparties/{id}
```

## 7. Приоритетный план доработок текущей реализации

### F0. Синхронизировать модели с backend workflow

- [ ] Переименовать deal types: `cash_in` -> `deposit`, `cash_out` -> `withdrawal`, `rearrangement` -> `transfer_city`.
- [ ] Обновить `DealStatus`: `new`, `fixed`, `balance_check`, `awaiting_payment`, `in_delivery`, `done`, `canceled`.
- [ ] Перенести `insufficient_usdt` из статуса в alert/flag модели сделки.
- [ ] Унифицировать `CurrencyCode` и `MoneyAmount`, убрать второй `CurrencyCode` из `balances`.
- [ ] Вынести `UserRole` под backend roles: `cashier`, `manager`, `accountant`, `owner`, `admin`.
- [ ] Обновить nav/RBAC под фактическую матрицу доступа.

### F1. Подготовить API-first слой

- [ ] Создать `src/api/httpClient.ts`: base URL, JWT, обработка 401/403, JSON errors.
- [ ] Создать `src/api/wsClient.ts` или hook `useDealsSocket`.
- [ ] Разделить сервисы на интерфейсы и реализации: `Mock*Service`, `Api*Service`.
- [ ] Добавить env-переключатель mock/API.
- [ ] Описать DTO, которые совпадают с API v1; после появления OpenAPI заменить ручные типы генерацией.
- [ ] Добавить loading/error/empty states на страницы, где сейчас данные приходят синхронно из mock.

### F2. Авторизация и права

- [ ] Добавить страницу/flow Telegram Login Widget.
- [ ] Хранить JWT-сессию и текущего пользователя через `GET /api/v1/me`.
- [ ] Реализовать `PermissionPolicy` на фронте.
- [ ] Фильтровать sidebar по роли и feature availability.
- [ ] Добавить route guards для `/accounting`, `/turnover`, `/reports`, `/settings`, `/income`.
- [ ] Обрабатывать API `403` без падения UI.

### F3. Read-only CRM поверх готового бота

Это первый этап с быстрой ценностью и минимальным риском.

- [ ] `/clients`: подключить `GET /clients`, `GET /clients/{id}`.
- [ ] В карточку клиента добавить реальные балансы, chat_id, историю транзакций/выписки.
- [ ] Комментарии клиента подключить к `client_comments` endpoints после появления таблицы.
- [ ] `/balances`: подключить `GET /balances?currency=&sign=`.
- [ ] `/`: dashboard v1 читать из готовых балансов клиентов, rate orders и курсов фирмы.
- [ ] Убрать mock-цифры, которые не имеют backend-источника, или пометить как placeholder.

### F4. Доска сделок realtime

- [ ] `/deals`: подключить `GET /deals` и фильтры `status/city/type/client_id/period`.
- [ ] Подписаться на `WS /ws/deals`, обновлять kanban без F5.
- [ ] Добавить карточку сделки с `legs`, `status_events`, Tronscan/payment watch.
- [ ] Реализовать смену статусов через `POST /deals/{id}/status`.
- [ ] Для `balance_check` показать alert `insufficient_usdt`, если API его вернул.
- [ ] При действиях менеджера показывать pending/success/error состояние, так как backend будет писать `tg_outbox`.

### F5. Новая сделка без расхождения схем

- [ ] Заменить локальный `TYPE_FIELDS` на `GET /deals/schema`.
- [ ] Привести форму к backend names: `deposit`, `withdrawal`, `transfer_city`.
- [ ] Контрагентов брать из `/admin/counterparties` или справочного endpoint.
- [ ] Клиента выбирать через API search.
- [ ] Расчет preview получать от backend или из schema-calculator контракта, но не держать самостоятельные бизнес-формулы.
- [ ] Submit отправлять в `POST /deals`, затем переходить в карточку/доску.
- [ ] Убрать `console.log('create deal payload')`.

### F6. Бухгалтерия, расходы, оборот, посещаемость

- [ ] `/expenses`: CRUD fixed/variable расходов, period filter, pie chart, кнопка добавления строки.
- [ ] `/accounting`: реальные `cash_desks`, `cash_desk_moves`, P&L, PDF export.
- [ ] `/turnover`: `capital_moves`, вклады/выводы владельцев, owner/admin only.
- [ ] `/attendance`: реальные пользователи, календарь месяца, переработки, сохранение изменений.
- [ ] `/cash`: решить, это отдельная страница касс или redirect/подраздел `/accounting`.
- [ ] `/income` и `/reports`: определить, отдельные разделы или часть бухгалтерии.
- [ ] `/settings`: пользователи, роли, города, контрагенты, курсы фирмы.

### F7. Убрать UI-дубли и привести код к SOLID

- [ ] Вынести `usePagination`, `TablePagination`, `PageSizeSelect`.
- [ ] Вынести `MetricCardModel` и базовый `MetricCard`.
- [ ] Унифицировать `SearchInput`, city/currency filters, table empty states.
- [ ] Вынести общие chart primitives: line/donut geometry, tooltip, legend.
- [ ] Разделить feature-компоненты на container/view, чтобы API-загрузка не смешивалась с таблицами.
- [ ] Оставить бизнес-правила в backend/use-cases; во фронте держать только view models и permissions.

### F8. Проверки и качество

- [ ] Обновить `README.md`: текущий Next.js фронт, команды, env, связь с backend.
- [ ] Оставить один пакетный менеджер. Сейчас есть `pnpm-lock.yaml` и `package-lock.json`; с учетом `packageManager: pnpm` лучше убрать npm lock после согласования.
- [ ] Добавить unit-тесты для frontend helpers: permissions, formatters, pagination.
- [ ] Добавить component-тесты для таблиц, формы сделки, route guards.
- [ ] Добавить smoke E2E: логин, клиенты, балансы, доска сделок, новая сделка.
- [ ] Запускать `pnpm lint` перед merge.

## 8. Что не надо делать на фронте

- Не реализовывать собственный денежный ledger.
- Не считать финальную прибыль сделки независимо от backend.
- Не хранить справочники валют/статусов/типов сделок отдельно от API/schema.
- Не делать `insufficient_usdt` отдельным статусом.
- Не подменять RBAC только скрытием пунктов меню. API все равно должен быть источником доступа.
- Не переносить Google Sheets-логику во фронт. На переходе ее читает backend.

## 9. Ближайший практичный backlog

1. Обновить типы сделок и статусы под `workflow_crm.md`.
2. Ввести frontend `PermissionPolicy` и роли `cashier/manager/accountant/owner/admin`.
3. Добавить API/mock переключатель в `src/services/index.ts`.
4. Подготовить DTO для `clients`, `balances`, `deals`.
5. Перевести `/clients` и `/balances` на API-ready сервисы первыми.
6. Перевести `/deals` на backend statuses и websocket-ready структуру.
7. Переделать `/deals/new` под `GET /deals/schema`.
8. Решить судьбу `/cash`, `/income`, `/reports`, `/settings`: скрыть или добавить заглушки.
9. Убрать `console.log` из пользовательских действий.
10. Обновить `README.md` и договориться, остается ли Next.js как основной frontend.
