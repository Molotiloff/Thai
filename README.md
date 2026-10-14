# SkyEX Thai CRM

Единый репозиторий backend/Telegram-бота и frontend тайского филиала SkyEX.
GitHub: https://github.com/Molotiloff/Thai
Production: https://thai-crm.work.gd

| Каталог | Назначение |
|---|---|
| `CRM THB/` | Python API, существующий бот main.py, сервисы, SQL и миграции |
| `bookkeeping thai/` | Next.js frontend и BFF |
| `.github/workflows/` | Общий CI для обеих частей |

В имени backend между CRM и THB стоит неразрывный пробел. Команды с путями
выполнять в кавычках. Каталоги приложений сохранены после объединения.

## Локальная подготовка

Backend из корня репозитория:

```bash
cd 'CRM THB'
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.thai.example .env.thai
```

Заполнить локальный .env.thai. PostgreSQL отдельный на **5433**, база thai_crm,
новый пользователь thai_crm_app. Существующий экземпляр на 5432 не использовать.
Миграции и запуск из backend-каталога:

```bash
.venv/bin/python -m db_asyncpg.bootstrap --schema thai --config .env.thai
.venv/bin/python main.py
```

Frontend в другом терминале из корня репозитория:

```bash
cd 'bookkeeping thai'
cp .env.example .env.local
pnpm install --frozen-lockfile
pnpm dev
```

Нужны Node 22/24 и pnpm 11.3.0. Frontend — порт 3001, API — 8001.
В frontend передаются только публичный OIDC Client ID, origin и серверный API URL.
Секрет OIDC, токен бота и реквизиты PostgreSQL остаются в backend .env.thai.

## Развёртывание

[Полный план](CRM%C2%A0THB/deploy/PRODUCTION_DEPLOY.md) и
[сверка схемы](CRM%C2%A0THB/deploy/thai/SCHEMA_VERIFICATION.md).
При получении одного Git-репозитория содержимое CRM THB используется как backend
release, содержимое bookkeeping thai — как frontend release. Это сохраняет
пути systemd из инструкции; не назначать корень монорепозитория backend release.

## Первый коммит

Репозиторий подготовлен на ветке main; origin указывает на Molotiloff/Thai.
Первый коммит и отправку выполнить из корня Thai после просмотра индекса:

```bash
git status --short
git diff --cached --stat
git diff --cached --check
git commit -m "Initial commit: SkyEX Thai CRM"
git push -u origin main
```

.gitignore исключает рабочие env и ключи, виртуальные окружения, node_modules,
сборки и кэши, runtime/media, дампы, финансовые выгрузки, логи и локальные IDE.
Санитизированные env-шаблоны, миграции/schema.sql, исходники, lockfile,
документация развёртывания и тесты входят в репозиторий.
.gitignore не заменяет проверку содержимого файлов перед публикацией.
Вложенные Git-репозитории удалены; фронт и бэк входят обычными файлами,
без submodules и отдельной истории.
