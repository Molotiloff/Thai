# SkyEX → CRM: план работ и workflow миграции

> Основан на: `CRM.pdf` (структура фронтенда), `Crm SkyEx (2).pdf` (планируемый flow),
> текущей кодовой базе бота, `workflow.md` (рефакторинг) и **реверс-инжиниринге
> боевой Google-таблицы бухгалтерии** (12 листов: Продажа 3949 строк, Покупка 1980,
> Сделки, Главная, Расходы, Прибыль, Оборотка, Посещаемость, Статистика,
> Контрагенты, стата, Данные) — формулы разобраны, модель подсчётов описана в разделе 4.
> Легенда: ✅ сделано · 🔄 в работе · ⬜ не начато · ❓ требует согласования

---

## 1. Цель и принцип миграции

**Что есть:** Telegram-бот (aiogram 3 + asyncpg + Postgres) с полноценным денежным ядром:
immutable-журнал `transactions`, кошельки клиентов, обменные и наличные заявки,
Google Sheets как «витрина» и ручной учёт сделок.

**Что хотим:** полноценная CRM с веб-фронтендом, при этом Telegram-бот остаётся
основным каналом приёма заявок. Целевой flow из `Crm SkyEx (2).pdf`:

```
Telegram Bot ──► Application Services / Use Cases ──► Postgres ◄── CRM API ◄── CRM Frontend
```

**Главный принцип: бот и CRM API — два входа в один и тот же слой сервисов.**
Кодовая база уже устроена как `handlers → services (use-cases) → db_asyncpg (ports/repositories)`,
поэтому мигрируем НЕ переписыванием, а добавлением второго транспорта (HTTP/WS)
поверх существующих сервисов. Google Sheets выводится из эксплуатации в конце,
после двойной записи и сверки.

**Целевой сценарий (из PDF):**
1. TG-бот создал заявку.
2. Backend сохранил сделку (`deals` + `deal_legs`) — «скелет заявки для CRM».
3. CRM видит заявку на активной доске (realtime, без F5).
4. Менеджер меняет статус / фиксирует курс / отмечает оплату.
5. Backend пишет `deal_status_events`.
6. Backend обновляет Telegram-карточку.
7. Backend отправляет клиенту уведомление.
8. При завершении фиксируется прибыль, касса, ссылка Tronscan/чек.

---

## 2. Целевая архитектура

### 2.1 Компоненты

```
┌────────────────────────────────────────────────────────────────────┐
│                     ОДИН ПРОЦЕСС (asyncio), позже можно разнести   │
│                                                                    │
│  aiogram Dispatcher          FastAPI (uvicorn)                     │
│  (polling, как сейчас)       REST /api/v1/* + WS /ws               │
│        │                            │                              │
│        ▼                            ▼                              │
│  ┌──────────────────────────────────────────────┐                 │
│  │       services/  (use-cases, БЕЗ Telegram-I/O)│                 │
│  │  exchange · cash_requests · wallets · crm(new)│                 │
│  │  client_balances · act_counter · payment_watch│                 │
│  └──────────────────────────────────────────────┘                 │
│        │                     │                                     │
│        ▼                     ▼                                     │
│  db_asyncpg (ports + repositories, asyncpg pool)                   │
│        │                                                           │
│        ▼                                                           │
│   PostgreSQL  ◄── tg_outbox (обновления карточек) ── воркер бота  │
└────────────────────────────────────────────────────────────────────┘
                              ▲
                              │ HTTPS (nginx)
                    Frontend: Next.js + React + TS
```

- **Backend CRM API** — FastAPI в этом же репозитории (`api/`), тот же пул asyncpg,
  те же репозитории. Причина выбора: async-native, Pydantic-схемы, OpenAPI из коробки,
  стек остаётся чисто питоновским.
- **Один процесс на старте.** `bot_app.run_app()` уже поднимает asyncio-loop —
  добавляем в него `uvicorn.Server.serve()` как ещё одну задачу. Это снимает вопрос
  «как CRM обновит Telegram-карточку»: экземпляр `Bot` доступен в том же процессе.
  Разнесение на два systemd-юнита — отдельный шаг (см. 2.4), когда появится нагрузка.
- **Frontend** — существующий Next.js-проект (уже приведён к API-first):
  потребляет `/api/v1` и `/ws` этого backend'а. Контракт — OpenAPI-схема FastAPI,
  типизированный клиент генерируется из неё (`openapi-typescript`), чтобы фронт и
  Pydantic-схемы не разъезжались. Auth: JWT в httpOnly-cookie (удобно для
  SSR/middleware Next.js), CORS в FastAPI под домен фронта.
- **Realtime** — WebSocket `/ws/deals`: backend пушит события `deal.created`,
  `deal.status_changed`, `deal.updated`. Источник событий — те же use-cases
  (паттерн: use-case пишет в БД → публикует событие в in-process `EventBus` →
  подписчики: WS-хаб, Telegram-нотификатор).

### 2.2 Синхронизация с Telegram: outbox

Действие менеджера в CRM должно поменять карточку в Telegram (шаги 6–7 flow).
Чтобы не терять обновления при рестарте и позволить в будущем разнести процессы:

- Таблица **`tg_outbox`** (id, kind, payload JSONB, status, attempts, created_at).
- Use-case в одной транзакции с бизнес-изменением кладёт запись в outbox.
- Воркер в процессе бота (по образцу `services/payment_watch/poller.py` —
  конструкция уже отработана) читает outbox и выполняет: `edit_message_text`
  карточки заявки, уведомление клиенту, сообщение в чат заявок.
- Идемпотентность правок Telegram уже решена: `utils/errors.suppress_telegram_edit_errors`.

### 2.3 Доступы (жёсткое требование из PDF)

> «Кассир и обычный менеджер не должен иметь доступ к статистикам и отчётам!!!»

RBAC на уровне API (dependency в FastAPI), роли:

| Роль | Сделки | Клиенты/Балансы | Расходы | Бухгалтерия/Отчёты | Оборот (вложения) | Админка |
|---|---|---|---|---|---|---|
| `cashier` | свои города, статусы | чтение | — | — | — | — |
| `manager` | полный доступ | полный | внесение | — | — | — |
| `accountant` | чтение | чтение | полный | полный | — | — |
| `owner` | полный | полный | полный | полный | полный | — |
| `admin` | всё | всё | всё | всё | всё | всё |

Аутентификация: **Telegram Login Widget** (проверка hash подписи токеном бота —
никаких паролей, юзеры уже живут в Telegram) → JWT-сессия. Таблица `managers`
(user_id, display_name) уже есть — расширяется до `users` с ролью и городами.

### 2.4 Деплой

Прод: noisy-gray, `/root/SkyEx`, `skyex-bot.service` (рестартует пользователь).
- Этап MVP: тот же юнит, тот же процесс (бот + API); nginx проксирует `/api` и `/ws`
  на backend, остальное — на Next.js (`output: 'standalone'` + отдельный
  systemd-юнит `skyex-crm-front.service`, либо `next export` в статику, если SSR
  не используется — ❓ по факту текущего проекта).
- Позже: отдельный `skyex-crm-api.service`, общение с ботом только через Postgres
  (outbox) — архитектура это уже позволяет.

---

## 3. Модель данных

### 3.1 Что переиспользуем как есть (schema.sql, 15 таблиц)

| Таблица | Роль в CRM |
|---|---|
| `clients`, `client_accounts` | Карточка клиента, балансы по валютам (страница «Клиенты», «Балансы») |
| `transactions` | Immutable-журнал: история операций, выписки, аудит (`balance_after`, `idempotency_key` — уже боевое) |
| `exchange_request_links` | Связка заявка ↔ Telegram-карточки ↔ номер таблицы; валюты/суммы/курс уже хранятся (`table_in_cur/out_cur/in_amount/out_amount/rate`) |
| `act_request_transactions` | Привязка транзакций к заявке (IN/OUT, ACTIVE/CANCELED) — готовая основа для «Сверки баланса» на доске сделок |
| `request_schedule_entries/boards` | Время/расписание выдач по городам — колонка «время» на доске сделок |
| `payment_watches`, `payment_watch_events` | Статус «ожидание оплаты», tx_hash для ссылки Tronscan в завершённой сделке |
| `rate_orders` | Ордера по курсу (виджет на Главной) |
| `managers` | Seed для `users` |
| `app_settings`, `live_messages` | Настройки; live-механика остаётся боту |
| `request_id_seq` | Сквозная нумерация сделок — сохраняем как номер сделки CRM |

### 3.2 Новые таблицы (миграциями, см. этап 0)

```sql
-- Пользователи CRM и роли -------------------------------------------------
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    tg_user_id BIGINT UNIQUE NOT NULL,          -- seed из managers.user_id
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('cashier','manager','accountant','owner','admin')),
    cities TEXT[] NOT NULL DEFAULT '{}',        -- для кассиров: свои города
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Сделки: единая шапка для всех 10 типов из CRM.pdf -------------------------
CREATE TABLE deals (
    id BIGSERIAL PRIMARY KEY,
    deal_no BIGINT NOT NULL DEFAULT nextval('request_id_seq'),  -- сквозной номер
    deal_type TEXT NOT NULL CHECK (deal_type IN
        ('sale','purchase','deposit','withdrawal','delivery','transfer_city',
         'conversion','yuan','invoice','profit')),
    city TEXT NOT NULL DEFAULT 'екб',
    client_id BIGINT REFERENCES clients(id),
    counterparty_id BIGINT REFERENCES counterparties(id),
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN
        ('new','fixed','awaiting_payment','balance_check','in_delivery',
         'done','canceled')),
    created_by BIGINT REFERENCES users(id),      -- «кто создал сделку» (из PDF)
    source TEXT NOT NULL DEFAULT 'crm'           -- 'tg_bot' | 'crm'
        CHECK (source IN ('tg_bot','crm')),
    comment TEXT,
    penalty NUMERIC(38,8),                       -- «потом добавим строчку штраф»
    tronscan_url TEXT,                           -- чек по usdt-расчётам
    -- типоспецифичные поля — JSONB, валидируются Pydantic-схемой по deal_type:
    body JSONB NOT NULL DEFAULT '{}',
    profit NUMERIC(38,8),                        -- авто-подсчёт, денормализовано для отчётов
    -- связь со старым миром (пока живут оба контура):
    exchange_client_req_id TEXT REFERENCES exchange_request_links(client_req_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON deals(status) WHERE status NOT IN ('done','canceled');
CREATE INDEX ON deals(client_id, created_at);
CREATE INDEX ON deals(deal_type, created_at);

-- Денежные «ноги» сделки: связь с журналом transactions ---------------------
CREATE TABLE deal_legs (
    id BIGSERIAL PRIMARY KEY,
    deal_id BIGINT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    transaction_id BIGINT REFERENCES transactions(id),
    direction TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
    currency_code TEXT NOT NULL,
    amount NUMERIC(38,8) NOT NULL,
    rate NUMERIC(38,8),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','CANCELED'))
);

-- История статусов (шаг 5 целевого flow) ------------------------------------
CREATE TABLE deal_status_events (
    id BIGSERIAL PRIMARY KEY,
    deal_id BIGINT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    old_status TEXT,
    new_status TEXT NOT NULL,
    actor_user_id BIGINT REFERENCES users(id),
    payload JSONB NOT NULL DEFAULT '{}',         -- зафиксированный курс, сумма оплаты…
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Контрагенты (в карточке сделки: «процент, который нужно отдать КТ») --------
CREATE TABLE counterparties (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    default_fee_percent NUMERIC(9,4),            -- фикс. процент из карточки КТ
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    comment TEXT
);

-- Кассы фирмы по городам и валютам (Бухгалтерия) -----------------------------
CREATE TABLE cash_desks (
    id BIGSERIAL PRIMARY KEY,
    city TEXT NOT NULL,
    currency_code TEXT NOT NULL,
    balance NUMERIC(38,8) NOT NULL DEFAULT 0,
    UNIQUE (city, currency_code)
);
CREATE TABLE cash_desk_moves (                   -- перемещения между кассами + аудит
    id BIGSERIAL PRIMARY KEY,
    from_desk_id BIGINT REFERENCES cash_desks(id),
    to_desk_id BIGINT REFERENCES cash_desks(id),
    amount NUMERIC(38,8) NOT NULL,
    balance_after_from NUMERIC(38,8),
    balance_after_to NUMERIC(38,8),
    deal_id BIGINT REFERENCES deals(id),
    actor_user_id BIGINT REFERENCES users(id),
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Расходы (страница «Расходы»: постоянные + переменные) ----------------------
CREATE TABLE expenses (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('fixed','variable')),
    category TEXT NOT NULL,                      -- для fixed: категория; для variable: назначение
    city TEXT,
    amount NUMERIC(38,8) NOT NULL,
    currency_code TEXT NOT NULL DEFAULT 'RUB',
    comment TEXT,
    expense_at DATE NOT NULL DEFAULT CURRENT_DATE,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Оборот: владельцы капитала и вложения (лист «Оборотка») --------------------
CREATE TABLE capital_owners (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,                   -- Алексей, Влад, Бабушка, Никита, Костя, Сочи, Иван…
    monthly_rate NUMERIC(9,4),                   -- «Залог под %» в месяц (0.02, 0.025…); NULL = без выплат
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE capital_moves (                     -- журнал вкладов/выводов (знаковая сумма, как в листе)
    id BIGSERIAL PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES capital_owners(id),
    amount NUMERIC(38,8) NOT NULL,               -- + вклад / − вывод
    move_at DATE NOT NULL DEFAULT CURRENT_DATE,
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE capital_payouts (                   -- фактические выплаты владельцам (правая табличка листа)
    id BIGSERIAL PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES capital_owners(id),
    period DATE NOT NULL,                        -- месяц выплаты
    accrued NUMERIC(38,8) NOT NULL,              -- начислено = вклад × ставка
    paid NUMERIC(38,8) NOT NULL DEFAULT 0,       -- фактически выплачено
    UNIQUE (owner_id, period)
);

-- Валютная позиция фирмы (реверс «Главной»: кол-во/в рубле/курс) --------------
-- Позиция ведётся методом средневзвешенной себестоимости: покупка увеличивает
-- qty и rub_cost, продажа списывает по среднему курсу (см. раздел 4.2).
CREATE TABLE firm_position_moves (
    id BIGSERIAL PRIMARY KEY,
    currency_code TEXT NOT NULL,                 -- USDT, EUR, USD_BL, USD_WH, CNY…
    deal_id BIGINT REFERENCES deals(id),
    kind TEXT NOT NULL CHECK (kind IN ('purchase','sale','adjust')),
    qty NUMERIC(38,8) NOT NULL,                  -- + покупка / − продажа
    rub_amount NUMERIC(38,8) NOT NULL,           -- рублёвая стоимость движения
    qty_after NUMERIC(38,8) NOT NULL,            -- денормализованный остаток (как balance_after в transactions)
    rub_cost_after NUMERIC(38,8) NOT NULL,       -- остаток рублёвой себестоимости
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON firm_position_moves(currency_code, id);

-- Внутренние балансы SkyEx («Балансы SkyEx» на Главной) -----------------------
-- Сотрудники/партнёры/техсчета: Баланс ВВ, Никита, Влад, Лев, Монах, Миша,
-- б_бабушка, б_сочи, б_костет, «разрыв 17.10»… Это НЕ клиенты и НЕ кассы.
CREATE TABLE internal_accounts (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'employee'
        CHECK (kind IN ('employee','partner','owner_pledge','tech')),  -- tech = разрывы/фиксации
    balance NUMERIC(38,8) NOT NULL DEFAULT 0,    -- RUB-эквивалент, знаковый
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE internal_account_moves (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES internal_accounts(id),
    amount NUMERIC(38,8) NOT NULL,
    balance_after NUMERIC(38,8) NOT NULL,
    deal_id BIGINT REFERENCES deals(id),
    actor_user_id BIGINT REFERENCES users(id),
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Маппинг клиент → контрагент с процентами (лист «Контрагенты», правая таблица)
CREATE TABLE client_kt_fees (
    id BIGSERIAL PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES clients(id),
    counterparty_id BIGINT NOT NULL REFERENCES counterparties(id),
    sale_fee NUMERIC(9,4),                       -- «% с продажи» — КТ-спред на единицу валюты
    purchase_fee NUMERIC(9,4),                   -- «% с покупки»
    UNIQUE (client_id, counterparty_id)
);

-- Посещаемость (страница «Посещаемость») -------------------------------------
CREATE TABLE attendance (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    work_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'work'          -- отметки из листа «Посещаемость»
        CHECK (status IN ('work','absent','flight','remote')),
    overtime_hours NUMERIC(5,2) NOT NULL DEFAULT 0,
    UNIQUE (user_id, work_date)
);

-- Комментарии в карточке клиента (из CRM.pdf) --------------------------------
CREATE TABLE client_comments (
    id BIGSERIAL PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    author_user_id BIGINT REFERENCES users(id),
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

-- Outbox для Telegram (см. 2.2) ----------------------------------------------
CREATE TABLE tg_outbox (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,                          -- 'edit_request_card' | 'notify_client' | ...
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','sent','failed')),
    attempts INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ
);
```

### 3.3 Типы сделок → поля `body` (по CRM.pdf) и авто-подсчёты

| Тип | Поля body | Авто-подсчёт (backend) | Чем считаем |
|---|---|---|---|
| Продажа `sale` | валюта, вход (= средневзвеш. курс позиции фирмы, авто, можно править), количество, выход, КТ + КТ-спред | сумма покупки = вход×кол-во; сумма продажи = выход×кол-во; спред = выход−вход; прибыль; КТ-сумма = КТ-спред×кол-во; наша прибыль = прибыль − КТ-сумма | новый `services/crm/deal_calc.py` + `firm_position_moves` (см. раздел 4.2); списание позиции по среднему курсу |
| Покупка `purchase` | валюта, сумма, курс, продавец | сумма в рублях = сумма×курс; пополнение позиции фирмы (qty+, rub_cost+) | там же |
| Внесение `deposit` | сумма, номинал?, комментарий? | — | **готово**: `services/cash_requests` (`create_cash_request`, `request_issue_service.deposit`) |
| Выдача `withdrawal` | сумма, номинал?, комментарий? | — | **готово**: `services/cash_requests` (`withdraw`) |
| Доставка `delivery` | сумма, номинал?, курьер (справочник users), вознаграждение курьера, адрес, дата/время | — | `cash_requests` + `request_schedule_service` (время уже умеет `/время`) |
| Перестановка `transfer_city` | город-получатель, наш %, комиссия КТ | сумма выдачи = сумма − наш % − комиссия КТ | база: `utils/city_cash_transfer` + `cash_desk_moves` |
| Конвертация `conversion` | валюта, сумма, курс на usdt, сумма usdt для КТ | прибыль = сумма usdt − сумма usdt КТ | `deal_calc.py` (формула тривиальна) |
| Юань `yuan` | сумма руб, курс usdt, сумма usdt для КТ | прибыль — аналогично | `deal_calc.py` |
| Инвойс `invoice` | валюта, сумма, курс usdt, сумма usdt для КТ | прибыль — аналогично | `deal_calc.py` |
| Прибыль `profit` | сумма (можно формулой), комментарий? | — | выражения уже парсит `utils/calc` (`/calc`, `(2+3)*100-50%`) — переиспользуем evaluator |

Карточка сделки на фронте меняется динамически по `deal_type` — одна форма,
схемы полей отдаёт backend (`GET /api/v1/deals/schema`), чтобы фронт и Pydantic
не разъезжались.

---

## 4. Бухгалтерская модель: как считает текущая Google-таблица

Реверс-инжиниринг формул боевой таблицы. Это спецификация для `services/crm/accounting_service.py`
и `deal_calc.py` — CRM должна давать **те же цифры, что и таблица**, иначе ей не будут доверять.

### 4.1 Листы таблицы → сущности CRM

| Лист | Что содержит | Куда мигрирует |
|---|---|---|
| Продажа (3949 стр.) | журнал продаж: дата, валюта, вход, кол-во, выход, спред, прибыль, клиент, город, КТ, КТ-спред | `deals` (type=`sale`) + `deal_legs` + `firm_position_moves` |
| Покупка (1980 стр.) | журнал покупок: валюта, сумма, курс, в рубле, продавец, город | `deals` (type=`purchase`) + `firm_position_moves` |
| Сделки | прочие сделки: Прибыль, Перестановка (+ города, КТ) | `deals` (type=`profit`/`transfer_city`/…) |
| Главная | дашборд: кассы RUB по городам, позиции валют, балансы клиентов и SkyEx, оборот, разрыв, прибыль по городам | `cash_desks`, `firm_position_moves`, `client_accounts`, `internal_accounts` + расчётные эндпоинты |
| Расходы | постоянные + переменные, категории, города, свод по категориям | `expenses` |
| Прибыль | дневная P&L: доход с продаж + доход со сделок − расход | расчётный view поверх `deals.profit` + `expenses` |
| Оборотка | вклады/выводы владельцев, доли, ставки, выплаты по месяцам | `capital_owners` + `capital_moves` + `capital_payouts` |
| Посещаемость | сотрудники × дни, часы переработки, отметки | `attendance` |
| Статистика / стата | объёмы и доход по валютам×городам, средний спред, доли; месячная сводная | расчётные эндпоинты `/accounting/*` |
| Контрагенты | объём/прибыль по клиентам; маппинг клиент→КТ→% | `counterparties` + `client_kt_fees` + отчёт |
| Данные | справочники: валюты, категории расходов, города, курьеры, владельцы, виды перестановок | seed-миграция (см. 4.6) |

### 4.2 Валютная позиция фирмы и себестоимость (ядро модели)

Таблица ведёт позицию по каждой валюте (USDT, EUR, USD BL, USD WH) методом
**средневзвешенной себестоимости** (weighted average cost):

```
Кол-во позиции      = Σ количеств покупок − Σ количеств продаж
Руб. стоимость      = Σ (сумма×курс) покупок − Σ (вход×кол-во) продаж
Средний курс фирмы  = Руб. стоимость / Кол-во          ← это «Вход» новой продажи
```

Механика сделок:

- **Покупка**: позиция `qty += сумма`, `rub_cost += сумма × курс`.
- **Продажа**: «Вход» автозаполняется текущим средним курсом (менеджер может
  переопределить — в таблице встречаются ручные входы). Списание:
  `qty −= кол-во`, `rub_cost −= вход × кол-во`. Далее:
  - `сумма покупки = вход × кол-во` (себестоимость проданного)
  - `сумма продажи = выход × кол-во`
  - `спред = выход − вход`, `прибыль = сумма продажи − сумма покупки`
  - если у клиента есть КТ: `КТ-сумма = КТ-спред × кол-во` (процент из
    `client_kt_fees`, можно переопределить руками), `наша прибыль = прибыль − КТ-сумма`.

В CRM это `firm_position_moves` (append-only журнал с `qty_after`/`rub_cost_after` —
та же конструкция, что `transactions.balance_after`). Позиция и средний курс —
`SELECT` последней строки, а не пересчёт всей истории.

### 4.3 Оборотка и балансовая сверка (формулы «Главной»)

Ключевой расчёт таблицы — **два независимых способа посчитать оборотный капитал
и контроль их расхождения**:

```
АКТИВЫ:
  RUB кассы          = Σ рублёвых касс по городам/локациям     → cash_desks (RUB)
  RUB в валюте       = Σ rub_cost валютных позиций             → firm_position_moves
  Общий RUB          = RUB кассы + RUB в валюте

ОБЯЗАТЕЛЬСТВА (знаковые):
  Балансы клиентов   = Σ балансов клиентов по всем валютам      → client_accounts (RUB-эквивалент)
  Балансы SkyEx      = Σ внутренних балансов                    → internal_accounts
  Общий Балансы      = Балансы клиентов + Балансы SkyEx

СВЕРКА:
  Факт. Оборот       = Общий RUB − Общий Балансы               (по активам)
  Оборот (теоретич.) = Σ Оборотка (вклады − выводы) + накопленная Общая Прибыль
  Разрыв             = Факт. Оборот − Оборот                   ← должен быть ≈ 0

СВЕРКА ПО ВАЛЮТЕ (для каждой валюты):
  Факт валюты        = позиция фирмы + Σ клиентских балансов в валюте
  Разрыв валюты      = фактический остаток кошелька − Факт валюты
                       (фактический остаток вводится руками или берётся из
                        payment_watch/Tronscan для USDT)
```

В CRM: эндпоинт `GET /api/v1/accounting/reconciliation` считает всё это одним
запросом; на дашборде Главной — плашка «Разрыв» с алертом при |разрыв| > порога
(❓ порог). Это главный контроль целостности учёта — сейчас его вручную
мониторят по ячейке.

### 4.4 Прибыль и статистика

```
Доход               = прибыль с продаж (Σ по листу Продажа) + прибыль со сделок (Σ по листу Сделки)
Общая Прибыль       = Доход − Σ Расходы
Прибыль по городу   = Σ прибыли продаж города + Σ прибыли сделок города
Дневная P&L         = за дату: доход с продаж + доход со сделок − расходы за дату
Месячный оборот     = Σ сумм продажи за месяц
Доходность          = Доход / Месячный оборот
Средний спред       = Доход по валюте / Руб. объём по валюте    (по городам и общий)
Доля города         = объём города / общий объём
По контрагентам     = объём продаж, прибыль, объём валюты по каждому клиенту
```

Всё это — SQL-агрегаты поверх `deals` (`profit`, `body`, `city`, `deal_type`,
`created_at`) и `expenses`. Реализация: `accounting_service.py` + при
необходимости материализованные view для месячной сводной («стата»).

### 4.5 Оборотка: владельцы, доли, выплаты

Лист «Оборотка» = журнал знаковых вкладов/выводов по владельцам + расчёты:

```
Вклад владельца     = Σ capital_moves владельца (знаковая)
Общая оборотка      = Σ по всем владельцам
Доля владельца      = вклад / общая
Платим в месяц      = вклад × месячная ставка владельца (у кого есть «залог под %»)
```

Плюс таблица фактических выплат по месяцам и сверка «начислено vs выплачено»
(в таблице сейчас: разница 69 893 ₽). В CRM: `capital_owners.monthly_rate`,
начисление за месяц — job или кнопка «закрыть месяц» → `capital_payouts.accrued`,
факт выплаты вносится руками → отчёт по задолженности перед владельцами.
Страница «Оборот»: круговой график долей — прямо из этих данных.

### 4.6 Справочники (лист «Данные» → seed-миграция)

- **Валюты позиций**: USDT, EUR, USD BL, USD WH, CNY.
- **Типы операций листа «Сделки»**: Перестановка, Инвойс, Разрыв, Доставка, Прибыль, Код —
  маппятся на `deal_type`; «Разрыв» и «Код» ❓ уточнить семантику (техтипы → `profit`/`adjust`?).
- **Виды перестановок**: Инвойс, Другой город, Доставка, Код, TRX, Конвертация, Юань,
  Обналичить — подтип в `body.transfer_kind`.
- **Категории расходов**: ~30 шт. (TRX, Аренда, Инкас члб, ЗП, AML, Реклама, Дивиденды,
  Переработка…) — таблица-справочник или CHECK-список, редактируемый в админке.
- **Города**: Екб, Члб, Мск, Спб, Тюмень, Краснодар, Новосибирск, Уфа.
- **Курьеры**: справочник для типа «Доставка» (Дмитрий, Александр, 1exch|…).
- **Владельцы оборотки**: Влад, Никита, Лев, Алексей, Костя, Иван, Сочи, Бабушка.
- **Отметки посещаемости**: Рабочий день / Пропуск / Полёт / Дистант + часы переработки.

### 4.7 Импорт истории из таблицы

Таблица экспортируется целиком (проверено: `export?format=xlsx` доступен).
Скрипт `scripts/import_sheets_history.py` (openpyxl, по образцу
`scripts/build_merge_patch_from_dumps.py` — генерирует SQL-патч + отчёт):

1. Справочники из «Данные» → seed.
2. «Контрагенты» → `counterparties` + `client_kt_fees` (маппинг клиент→КТ→%).
3. «Покупка» → `deals(purchase)` + реплей `firm_position_moves` в хронологии.
4. «Продажа» → `deals(sale)` + реплей списаний (вход берём из строки, НЕ пересчитываем —
   история должна сойтись 1:1).
5. «Сделки» → `deals(profit/transfer_city/…)`.
6. «Расходы» → `expenses`, «Оборотка» → `capital_moves`/`capital_owners`, «Посещаемость» → `attendance`.
7. Кассы и «Балансы SkyEx» с «Главной» → начальные `cash_desks`/`internal_accounts`.
8. **Валидация**: пересчитать агрегаты из БД и сверить с ячейками «Главной»
   (Факт. Оборот, позиции по валютам, прибыль по городам, дневная P&L) — расхождение
   должно быть 0 (кроме известного «Разрыва»). Отчёт сверки — как
   `dumps/merge_old_into_new_report.txt`.

Грязные данные, которые всплывут при импорте (видны уже сейчас): строки-агрегаты
внутри журналов («Май» вместо даты в «Покупке»), пустые клиенты, ручные
корректировки «разрыв 17.10 / 24.09 / 14.12», клиент «13.0». Правило: не чиним
молча — импортируем в техсчета/`adjust` и выводим в отчёт.

---

## 5. Карта переиспользования готового кода

| Что нужно CRM | Готовая конструкция | Что дорабатываем |
|---|---|---|
| Движение денег по клиенту (любая сделка) | `TransactionsRepo._apply_delta / deposit / withdraw` — атомарно, идемпотентно (`uq_tx_client_idem`, фикс 7-bis), `balance_after` | ничего; deal_legs просто ссылаются на `transaction_id` |
| Создание/отмена/правка обменной заявки | `services/exchange`: `create_exchange_request`, `cancel_exchange_request`, `edit_exchange_request`, `ExchangeBalanceService.apply_create/apply_cancel/apply_edit_delta`, `ExchangeCalculator` | вынести Telegram-I/O (этап 0), вызывать из API |
| Наличные заявки, выдача, завершение | `services/cash_requests`: `create_cash_request`, `edit_cash_request`, `request_issue_service`, `request_deal_done/cancel_service`, `request_schedule_service` | то же |
| Кошельки: пополнение/списание/undo/выписки | `services/wallets`: `WalletService`, `CurrencyMutationService.apply_external_currency_change`, `undo_service`, `utils/statements` | обёртки-эндпоинты |
| Страница «Балансы» (ненулевые, сортировка по валюте/сумме) | `services/client_balances/query_service.balances_by_client`, `filter_service`, `nonzero_wallet_query_service`, `ClientsRepo.balances_by_client` | JSON-сериализация вместо текста |
| Карточка клиента: имя/чат/группа/история | `ClientsRepo` (`ensure_client`, `list_clients*`, `snapshot_wallet`, `set_client_group_by_chat_id`), `TransactionsRepo.history/export_transactions` | «прибыль по клиенту» — агрегат по `deals.profit`; комментарии — новая таблица |
| Автосоздание клиента при добавлении бота в чат | уже работает (`/start`, `ensure_client`) — требование PDF выполнено | — |
| «Ожидание оплаты» + чек Tronscan | `services/payment_watch` (+ фиксы 429/ретраев), `payment_watch_events.tx_hash` | линк watch → deal_id |
| Курс фирмы на Главной | `gutils/sheets` (чтение `GOOGLE_BALANCE_CELLS_JSON`) | на переходный период — Sheets; после C4.2 средний курс считается из `firm_position_moves` (раздел 4.2), ручные курсы — в `app_settings` |
| Номера сделок | `request_id_seq` (монотонный, с 100000) | deal_no = nextval |
| «Сверка баланса» на доске | `act_request_transactions` + `services/act_counter` (текущий остаток по акту) | плашка «недостаточно Теза» → правило поверх act-остатка + вывод в чат заявок через outbox |
| Формулы в полях сумм («можно формулой») | `utils/calc` — безопасный evaluator выражений | вызвать из deal_calc |
| Ордера по курсу (виджет) | `services/rate_order`, `rate_orders` | read-only эндпоинт |
| Правка Telegram-карточек из CRM | тексты карточек: `services/exchange/text_builder`, `cash_requests/legacy_request_messages`, `request_table/message_builder`; подавление benign-ошибок: `utils/errors` | вызываются из outbox-воркера |
| Ежедневная сводка | `services/daily_balances_scheduler`, `client_balances/daily_report_service` | те же данные → дашборд Главной |
| PDF-отчёт по расходам («поделиться — красивый pdf») | `scripts/render_payment_receipt.py` — уже есть генерация изображений чеков (Pillow) | для PDF взять weasyprint/reportlab ❓; HTML-шаблон отчёта → PDF |
| Google Sheets на переходный период | `gutils/requests_sheet_gateway`, `services/request_table` | двойная запись: сделка → и в `deals`, и в Sheets, пока не сверимся |

**Чего в кодовой базе нет совсем (пишем с нуля):** FastAPI-слой, RBAC/JWT, WS-хаб,
EventBus, deal_calc для sale/purchase/conversion/yuan/invoice, кассы фирмы
(`cash_desks`), расходы, оборот, посещаемость, P&L-отчёты, фронтенд.

---

## 6. Структура репозитория (целевая)

```
SkyEX/
├── main.py                    # запускает бот + API в одном loop
├── bot_app.py                 # как сейчас + task для uvicorn
├── api/                       # NEW: CRM API
│   ├── app.py                 # FastAPI factory, CORS, lifespan (общий pool)
│   ├── deps.py                # auth-dependencies, get_current_user, require_role
│   ├── auth/                  #   telegram login widget → JWT
│   ├── ws.py                  #   WS-хаб /ws/deals (подписка на EventBus)
│   ├── schemas/               #   Pydantic: DealCreate по deal_type, DTO
│   └── routers/
│       ├── dashboard.py       # Главная: кассы, города, сводка, прибыль д/н/м
│       ├── deals.py           # CRUD сделок, смена статуса, доска
│       ├── clients.py         # клиенты, карточка, комментарии
│       ├── balances.py        # ненулевые балансы (страница «Балансы»)
│       ├── expenses.py        # расходы + круговой график (данные)
│       ├── accounting.py      # кассы, перемещения, P&L, PDF-экспорт
│       ├── turnover.py        # оборот: вложения владельцев
│       ├── attendance.py      # посещаемость
│       └── admin.py           # users, роли, контрагенты, настройки
├── services/
│   ├── crm/                   # NEW: use-cases CRM
│   │   ├── deal_service.py    # create/update/change_status → events → outbox
│   │   ├── deal_calc.py       # авто-подсчёты по 10 типам (см. 3.3)
│   │   ├── event_bus.py       # in-process pub/sub
│   │   ├── accounting_service.py  # кассы, P&L
│   │   └── report_pdf.py      # PDF-отчёты
│   ├── tg_outbox/             # NEW: воркер отправки в Telegram (по образцу payment_watch/poller)
│   └── ... (существующие без изменений по смыслу)
├── db_asyncpg/
│   ├── migrations/            # Alembic (этап 0) — versions/0001_baseline.py = текущий schema.sql
│   └── repositories/          # + deals.py, users.py, expenses.py, cash_desks.py,
│                              #   capital.py, attendance.py, outbox.py, counterparties.py
└── (frontend — существующий Next.js-проект, API-first; свой репозиторий/каталог)
```

### Страницы фронта (Next.js, роуты по PDF)

```
app/
├── (dashboard)/         # Главная: балансы касс/городов, факт. кол-во валюты, сводка, разрыв
├── deals/               # доска активных сделок + модалка «Новая сделка»
│   └── [id]/            # карточка сделки — динамическая форма по deal_type
├── balances/            # клиенты с ненулевыми балансами, ссылка на чат
├── expenses/            # 2 таблицы (пост./перем.) + pie chart за период
├── attendance/          # календарь по сотрудникам + переработки
├── turnover/            # вложения владельцев, вклады/выводы   [owner+]
├── accounting/          # кассы, позиции, сверка, P&L, статистика, PDF [accountant+]
└── clients/             # список + поиск
    └── [id]/            # карточка клиента: балансы, история, комментарии
```

Инфраструктура фронта: типизированный клиент из OpenAPI (`api/`), подписка на
`/ws/deals` с инвалидацией кэша данных, скрытие разделов по роли из `GET /me`
(жёсткое ограничение всё равно на backend-RBAC).

### Доска сделок (статусы из Crm SkyEx (2).pdf)

Колонки/фильтры дашборда активных сделок:
`new` → `fixed` («фикс», курс с клиентом зафиксирован) → `balance_check`
(«сверка баланса» — считаем остаток Теза с учётом предыдущих активных заявок;
если недостаточно — плашка «на откуп» + сообщение в чат заявок через outbox) →
`awaiting_payment` («ожидание оплаты», привязка к payment_watch) → `done`
(данные сделки + ссылка Tronscan) / `canceled`.

---

## 7. API (эскиз v1)

```
POST   /api/v1/auth/telegram            # login widget payload → JWT
GET    /api/v1/me

GET    /api/v1/dashboard                # кассы, города, сводка, прибыль д/н/м   [accountant+]
GET    /api/v1/dashboard/rates          # факт. количество валюты + курсы фирмы  [manager+]

GET    /api/v1/deals?status=&city=&type=&client_id=&period=
POST   /api/v1/deals                    # создать (модалка «Новая сделка»)
GET    /api/v1/deals/schema             # схемы полей по deal_type
GET    /api/v1/deals/{id}               # карточка + legs + status_events + tronscan
PATCH  /api/v1/deals/{id}               # редактирование (кликом по карточке)
POST   /api/v1/deals/{id}/status        # смена статуса: fix / paid / done / cancel
WS     /ws/deals                        # realtime доски

GET    /api/v1/clients?search=&group=
POST   /api/v1/clients                  # «добавить клиента руками»
GET    /api/v1/clients/{id}             # имя, chat_id, балансы, прибыль, сделки
PATCH  /api/v1/clients/{id}             # переименовать, группа
POST   /api/v1/clients/{id}/comments    · DELETE /comments/{cid}

GET    /api/v1/balances?currency=&sign= # страница «Балансы» (nonzero)

GET/POST/PATCH/DELETE /api/v1/expenses  # kind=fixed|variable                    [accountant+]
GET    /api/v1/expenses/summary?period= # данные для pie chart

GET    /api/v1/accounting/desks         # кассы по городам/валютам               [accountant+]
POST   /api/v1/accounting/moves         # перемещения между кассами
GET    /api/v1/accounting/positions     # позиции фирмы: кол-во, руб, средний курс (4.2)
GET    /api/v1/accounting/internal      # внутренние балансы SkyEx
GET    /api/v1/accounting/reconciliation# Факт. Оборот / Оборот / Разрыв (+по валютам, 4.3)
GET    /api/v1/accounting/pnl?period=   # дневная/месячная P&L (4.4)
GET    /api/v1/accounting/statistics?period=  # спреды, доли, доход по валютам×городам
GET    /api/v1/accounting/counterparties      # объём/прибыль по клиентам и КТ
GET    /api/v1/accounting/report.pdf    # экспорт PDF

GET/POST /api/v1/turnover               # вложения/выводы владельцев             [owner+]
GET/PUT  /api/v1/attendance?month=      # календарь посещаемости                 [manager+]
GET/POST/PATCH /api/v1/admin/users      · /admin/counterparties                  [admin]
```

---

## 8. План работ по этапам

### Этап C0 — Фундамент (разблокирует всё остальное) ⬜

Частично совпадает с этапами 2–4 из `workflow.md` — теперь они на критическом пути CRM.

- ⬜ **C0.1 Alembic** (закрывает workflow.md 2.2): baseline = текущий `schema.sql`,
  дальше все новые таблицы (раздел 3.2) — только миграциями. Убрать ленивые `_ensure_*_table()`.
- ⬜ **C0.2 Тесты денежного ядра** (workflow.md 3.1–3.2): `_apply_delta`,
  `ExchangeCalculator`, apply_create/cancel/edit — **до** того, как появится второй
  вызывающий (API). pytest + pytest-asyncio + testcontainers.
- ⬜ **C0.3 Вынести Telegram-I/O из use-cases** (workflow.md 4.2): сервисы
  `exchange`/`cash_requests` возвращают результат + список уведомлений, отправка —
  на границе (handler или outbox-воркер). Без этого API не сможет вызвать
  `create_exchange_request` — тот шлёт сообщения сам.
- ⬜ **C0.4 Миграции новых таблиц:** `users`, `deals`, `deal_legs`,
  `deal_status_events`, `counterparties`, `tg_outbox` (остальные — по мере этапов).
- ⬜ **C0.5 Скелет `api/`:** FastAPI + lifespan на общем пуле, uvicorn-task в
  `bot_app`, `/api/v1/health`, auth (Telegram Login → JWT), RBAC-dependency,
  seed `users` из `managers`.

**Выход этапа:** бот работает как раньше; рядом отвечает авторизованный API.

### Этап C1 — Read-only CRM (быстрая ценность) ⬜

Ничего не пишет — только читает готовые данные; риск нулевой.

- ⬜ **C1.1** Эндпоинты: клиенты, карточка клиента, балансы (nonzero), история
  транзакций/выписки — всё поверх готовых `ClientsRepo`/`client_balances`/`TransactionsRepo`.
- ⬜ **C1.2** Подключение существующего Next.js-фронта к API: генерация
  типизированного клиента из OpenAPI, логин-flow (Telegram Login → JWT в
  httpOnly-cookie), страницы «Клиенты» и «Балансы» на живых данных.
- ⬜ **C1.3** Дашборд «Главная» v1: балансы клиентов, курсы фирмы (чтение из Sheets
  через `gutils`, как делает бот), активные заявки из `exchange_request_links`/`request_schedule_entries`.
- ⬜ **C1.4** nginx на проде: `/api` + `/ws` → backend, остальное → Next.js
  (standalone-юнит или статический export, см. 2.4).

**Выход этапа:** менеджеры смотрят клиентов/балансы в вебе вместо запросов боту.

### Этап C2 — Доска сделок + связка с ботом (ядро CRM) ⬜

- ⬜ **C2.1 `deal_service` + EventBus + WS-хаб:** CRUD сделок, `deal_status_events`,
  пуш в `/ws/deals`.
- ⬜ **C2.2 Бот пишет в `deals`:** в `create_exchange_request` /
  `create_cash_request` после успеха — создание записи deal (`source='tg_bot'`,
  линк `exchange_client_req_id`). Двусторонняя видимость: заявка из TG сразу на доске.
- ⬜ **C2.3 Outbox-воркер:** действия менеджера в CRM (фикс, оплата, завершение,
  отмена) правят Telegram-карточку и шлют уведомление клиенту. Тексты — готовые
  text_builder'ы.
- ⬜ **C2.4 Статусная модель доски:** фикс / сверка баланса (через
  `act_request_transactions` + правило «недостаточно Теза» → плашка + сообщение
  в чат заявок) / ожидание оплаты (линк на `payment_watches`) / завершена
  (+ Tronscan из `payment_watch_events`).
- ⬜ **C2.5 Отмена/редактирование из CRM** → существующие
  `cancel_exchange_request`/`edit_*` (компенсирующие транзакции уже реализованы).

**Выход этапа:** целевой flow из PDF (шаги 1–8) работает end-to-end.

### Этап C3 — Карточка сделки: все 10 типов ⬜

- ⬜ **C3.1** `deal_calc.py`: формулы из 3.3 + Pydantic-схемы по типам +
  `GET /deals/schema`; evaluator формул — `utils/calc`.
- ⬜ **C3.2** Динамическая форма «Новая сделка» на фронте (тип → набор полей,
  клиент — выпадающий список с поиском, контрагент с % из карточки).
- ⬜ **C3.3** Типы поверх готовых сервисов: внесение/выдача/доставка →
  `cash_requests` (курьер, вознаграждение, адрес — новые поля body + user-справочник);
  продажа/покупка → `exchange`.
- ⬜ **C3.4** Новые типы: перестановка (`cash_desk_moves`), конвертация/юань/инвойс/прибыль
  (`deal_calc` + `deal_legs`).
- ⬜ **C3.5** Миграции: `cash_desks`, `cash_desk_moves`.

### Этап C4 — Бухгалтерия, оборотка, отчёты (спека — раздел 4) ⬜

- ⬜ **C4.1** Миграции: `expenses`, `capital_owners/moves/payouts`,
  `firm_position_moves`, `internal_accounts(+moves)`, `client_kt_fees`,
  `attendance`, `client_comments` + seed справочников из «Данные» (4.6).
- ⬜ **C4.2 Валютная позиция фирмы** (4.2): `firm_position_moves` + сервис
  средневзвешенной себестоимости; сделки sale/purchase из C3 подключаются к
  позиции («Вход» автозаполняется средним курсом); тесты на реплей
  покупка→продажа→средний курс (эталон — цифры из таблицы).
- ⬜ **C4.3** Кассы по городам/валютам + перемещения; внутренние балансы SkyEx
  (`internal_accounts`); «фактическое количество валюты» на Главной — позиция
  фирмы + клиентские остатки (формула «Факт валюты», 4.3).
- ⬜ **C4.4 Балансовая сверка**: `GET /accounting/reconciliation` — Общий RUB,
  Общий Балансы, Факт. Оборот, Оборот (вложения+прибыль), **Разрыв** с алертом;
  разрыв по валютам (факт кошелька — руками или из payment_watch для USDT).
  Это замена ручного мониторинга ячейки «Разрыв» на Главной.
- ⬜ **C4.5** Расходы: две таблицы (постоянные/переменные) + pie chart за период,
  строка «Добавить», справочник категорий (~30 из «Данные»), свод по категориям.
- ⬜ **C4.6** P&L и статистика (4.4): дневная P&L (аналог листа «Прибыль»),
  месячная сводная («стата»), прибыль по городам, средний спред и доли по
  валютам×городам («Статистика»), доходность, отчёт по контрагентам
  (объём/прибыль по клиентам). Графики прибыли — в разделе Бухгалтерия.
- ⬜ **C4.7** Оборотка (4.5): владельцы со ставками, вклады/выводы, доли
  (круговой график), начисление «платим в месяц», журнал фактических выплат,
  отчёт «начислено vs выплачено». Только `owner`/`admin`.
- ⬜ **C4.8** PDF-экспорт отчёта (расходы «поделиться», P&L).
- ⬜ **C4.9** Посещаемость: календарь месяца по сотрудникам, отметки
  (работа/пропуск/полёт/дистант) + переработки в часах.

### Этап C5 — Импорт истории, вывод Google Sheets и финализация ⬜

- ⬜ **C5.0** Импорт истории из таблицы (`scripts/import_sheets_history.py`, спека 4.7):
  журналы Продажа/Покупка/Сделки/Расходы/Оборотка → БД, начальные остатки касс
  и внутренних балансов с «Главной», валидация агрегатов против ячеек таблицы.
- ⬜ **C5.1** Двойная запись: «Занести в таблицу» пишет и в Sheets, и в `deals`
  (уже с C2.2); периодическая сверка Sheets ↔ БД — тот же валидатор, что в C5.0.
- ⬜ **C5.2** Курсы фирмы переезжают из Sheets в `app_settings` + редактор в CRM;
  `gutils` читает fallback.
- ⬜ **C5.3** После N недель чистой сверки — отключить запись в Sheets, кнопку
  «Занести в таблицу» заменить на «Провести» (пишет только в `deals`).
- ⬜ **C5.4** Разнести процессы: `skyex-crm-api.service` отдельно от бота
  (общение уже только через Postgres/outbox).
- ⬜ **C5.5** Хвосты: аудит-лог действий в CRM, rate-limit API, бэкапы
  (dumps/ уже практикуются), мониторинг.

### Зависимости этапов

```
C0 ──► C1 ──► C2 ──► C3 ──► C5
              └────► C4 ────┘      (C4 не зависит от C3, можно параллельно)
```

---

## 9. Риски и открытые вопросы ❓

1. **C0.3 (вынос Telegram-I/O) — самый дорогой пункт фундамента**: `edit_exchange_request`
   361 строка, `create_exchange_request` 260 (workflow.md 4.3). Стратегия: не
   рефакторить всё — для C2 достаточно, чтобы use-case *опционально* принимал
   notifier-интерфейс; полная чистка — по ходу.
2. **Двойной контур сделок на переходный период** (exchange_request_links + deals):
   принято осознанно, линк-колонка есть; убрать старый контур можно только после C5.
3. ~~Кассы: начальные остатки~~ — решено: импорт с «Главной» скриптом C5.0 (спека 4.7);
   ❓ остаётся только выбрать дату фиксации среза.
4. ~~Импорт истории из Sheets~~ — решено: таблица публично экспортируется, формулы
   разобраны, план импорта и валидации — раздел 4.7. ❓ подтвердить семантику
   типов «Разрыв» и «Код» из листа «Сделки» и судьбу техстрок («разрыв 17.10» и т.п.).
5. **Роли**: матрица в 2.3 — черновик, ❓ подтвердить (особенно кто видит «Оборот»).
6. **PDF-библиотека**: weasyprint (HTML→PDF, красиво, тяжёлые зависимости) vs
   reportlab (легче, вёрстка руками). Рекомендация: weasyprint.
7. ~~Frontend-стек~~ — решено: существующий Next.js-проект (API-first), контракт
   через OpenAPI-схему FastAPI. ❓ уточнить: используется ли SSR (влияет только на
   способ деплоя — standalone-процесс vs статический export, см. 2.4) и где живёт
   репозиторий фронта.
8. **Telegram Login Widget** требует домен, привязанный к боту (`/setdomain` у BotFather),
   и HTTPS на noisy-gray — заложить в C0.5/C1.4.
9. **Таблица уже расходится сама с собой** — аргумент за миграцию, но и риск для
   импорта: «Разрыв USDT» на Главной считается от захардкоженного фактического
   остатка (`=14-E11` при расчётном 18 966), формулы «за день» бьются по вбитым
   номерам строк (`J359:J1001`), в журналах есть строки-агрегаты («Май» вместо даты)
   и ручные корректировки. При валидации импорта (4.7) сверять по помесячным
   агрегатам, а не по «дневным» ячейкам; все несходимости — в отчёт, решение по
   каждой принимает владелец.
10. **Точность вычислений**: таблица считает в float, CRM будет считать в `NUMERIC` —
   копеечные расхождения со «старыми» цифрами неизбежны; допуск сверки ❓ (предложение: 1 ₽ на строку, 100 ₽ на агрегат).
