# Обновление действующей Thai CRM после рефакторинга

Процедура подготовлена для коммита `384b42217558daf173e5d57142198ed7f0ef20c4`
из `origin/main`. Пересчёт входит в этот коммит. Доставка карточек расходов из
локальной ветки `codex/stage-6-expense-card-delivery` (`9ed1949`) предназначена
для отдельного релиза после публикации и проверок её коммита. Общий порядок
обслуживания и восстановления описан в [production runbook](../PRODUCTION_DEPLOY.md).

У процедуры одна совместимая схема `thai_0027` для старого и нового backend.
При другой ревизии остановиться на проверке и сначала пройти отдельную процедуру
обновления схемы. Порядок переключения здесь важен. Фронтенд этого релиза
использует `GET /api/v1/clients/options` из нового backend.

## 1. Подготовить релизы до остановки сервисов

Команды от root на сервере. Для другого расположения Git-репозитория заменить
путь `/root/Thai`. Скрипт сохраняет старые и новые пути для переключения и
отката. Вывод версий и ревизии записать в журнал релиза.

```bash
bash <<'BASH'
set -euo pipefail
umask 077
export PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin:$PATH
base=/opt/skyex-thai
repo=/root/Thai
commit=384b42217558daf173e5d57142198ed7f0ef20c4
state=/root/skyex-thai-384b422.state
test ! -e "$state"
test -L "$base/current"
test -L "$base/frontend-current"
old_backend=$(readlink -f "$base/current")
old_frontend=$(readlink -f "$base/frontend-current")
test -x "$old_backend/.venv/bin/python"
test -f "$old_frontend/.next/standalone/server.js"
systemctl is-active --quiet skyex-thai-backend
systemctl is-active --quiet skyex-thai-frontend
python3.14 --version
node --version
test "$(node -p 'process.versions.node.split(".")[0]')" = 22
test "$(pnpm --version)" = 11.3.0
pg_dump --version
pg_restore --version

git -C "$repo" fetch origin refs/heads/main:refs/remotes/origin/main
git -C "$repo" merge-base --is-ancestor "$commit" refs/remotes/origin/main
release="$(date -u +%Y%m%dT%H%M%SZ)-${commit:0:8}"
new_backend="$base/releases/$release"
new_frontend="$base/frontend-releases/$release"
test ! -e "$new_backend"
test ! -e "$new_frontend"
install -d -o skyex-thai -g skyex-thai -m 0750 "$new_backend" "$new_frontend"
git -C "$repo" archive "$commit" $'CRM\u00a0THB' | tar -xf - -C "$new_backend" --strip-components=1
git -C "$repo" archive "$commit" 'bookkeeping thai' | tar -xf - -C "$new_frontend" --strip-components=1
chown -hR skyex-thai:skyex-thai "$new_backend" "$new_frontend"

runuser -u skyex-thai -- env PATH="$PATH" bash -euo pipefail -c '
  cd "$1"
  ln -s /opt/skyex-thai/shared/.env.thai .env.thai
  ln -s /opt/skyex-thai/shared/runtime .runtime
  python3.14 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python -m pip check
' bash "$new_backend"

for backend in "$old_backend" "$new_backend"; do
  (
    cd "$backend"
    head=$(runuser -u skyex-thai -- .venv/bin/python -c \
      'from db_asyncpg.migrator import AlembicMigrator; print(AlembicMigrator("", schema="thai").head_revision())')
    test "$head" = thai_0027
    runuser -u skyex-thai -- .venv/bin/python -m db_asyncpg.bootstrap --schema thai --config .env.thai --status
  ) | python3.14 -c '
import json
import sys

status = json.load(sys.stdin)
print(status)
if status != {"schema": "thai", "revision": "thai_0027", "database": "thai_crm", "port": 5433}:
    sys.exit("Unexpected Thai database or revision")
'
done

runuser -u skyex-thai -- env PATH="$PATH" bash -euo pipefail -c '
  cd "$1"
  set -a
  . /opt/skyex-thai/shared/frontend.env
  set +a
  pnpm install --frozen-lockfile
  pnpm run typecheck
  pnpm exec eslint . --max-warnings 0
  pnpm run test:forms
  pnpm run test:telegram-login
  pnpm run build
  test -f .next/standalone/server.js
' bash "$new_frontend"

printf 'base=%q\nrelease=%q\nold_backend=%q\nold_frontend=%q\nnew_backend=%q\nnew_frontend=%q\n' \
  "$base" "$release" "$old_backend" "$old_frontend" "$new_backend" "$new_frontend" > "$state"
printf 'Готовы каталоги %s и %s\n' "$new_backend" "$new_frontend"
BASH
```

## 2. Сделать backup и переключить backend

Перед этим шагом согласовать короткое окно остановки Thai. В случае ошибки
после начала остановки выполнить откат из раздела ниже. Старый frontend
запускается обратно после готовности нового backend.

```bash
bash <<'BASH'
set -euo pipefail
. /root/skyex-thai-384b422.state
test "$(readlink -f "$base/current")" = "$old_backend"
test "$(readlink -f "$base/frontend-current")" = "$old_frontend"
backup="/var/backups/skyex-thai/$release-before.dump"
test ! -e "$backup"
test ! -e "$backup.json"
systemctl stop skyex-thai-frontend skyex-thai-backend
(
  cd "$old_backend"
  runuser -u skyex-thai -- .venv/bin/python -m app.thai_maintenance \
    --config .env.thai backup --output "$backup"
)
pg_restore --list "$backup" >/dev/null
python3.14 - "$backup" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

dump = Path(sys.argv[1])
meta = json.loads(Path(str(dump) + '.json').read_text())
with dump.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
if meta['family'] != 'thai' or meta['revision'] != 'thai_0027':
    sys.exit('Unexpected backup schema or revision')
if meta['sha256'] != digest:
    sys.exit('Backup checksum mismatch')
print('Backup checksum OK')
PY
ln -s "$new_backend" "$base/current.$release"
mv -Tf "$base/current.$release" "$base/current"
systemctl start skyex-thai-backend
curl --noproxy '*' --fail --retry 20 --retry-connrefused --retry-delay 1 --max-time 10 \
  http://127.0.0.1:8001/health/ready
systemctl start skyex-thai-frontend
systemctl is-active skyex-thai-backend skyex-thai-frontend
curl --noproxy '*' --fail --retry 8 --retry-connrefused --retry-delay 1 --max-time 15 \
  -o /dev/null https://thai-crm.work.gd/login
BASH
```

## 3. Переключить frontend и принять релиз

```bash
bash <<'BASH'
set -euo pipefail
. /root/skyex-thai-384b422.state
test "$(readlink -f "$base/current")" = "$new_backend"
test "$(readlink -f "$base/frontend-current")" = "$old_frontend"
curl --noproxy '*' --fail --max-time 10 http://127.0.0.1:8001/health/ready
systemctl stop skyex-thai-frontend
ln -s "$new_frontend" "$base/frontend-current.$release"
mv -Tf "$base/frontend-current.$release" "$base/frontend-current"
systemctl start skyex-thai-frontend
systemctl is-active skyex-thai-backend skyex-thai-frontend
curl --noproxy '*' --fail --retry 8 --retry-connrefused --retry-delay 1 --max-time 15 \
  -o /dev/null -w 'HTTPS /login: %{http_code}\n' https://thai-crm.work.gd/login
login_url=$(curl --noproxy '*' --fail --silent --show-error --location --max-redirs 2 \
  --proto-redir '=https' --max-time 15 -o /dev/null -w '%{url_effective}' https://thai-crm.work.gd/)
test "$login_url" = https://thai-crm.work.gd/login
journalctl -u skyex-thai-backend -u skyex-thai-frontend --since '15 minutes ago' --no-pager -n 120
cd "$new_backend"
runuser -u skyex-thai -- .venv/bin/python -m app.thai_maintenance --config .env.thai check \
  --frontend-env "$base/shared/frontend.env" --frontend-url http://127.0.0.1:3001 \
  --output ".runtime/production-readiness-$release.json"
BASH
```

В рабочей CRM проверить Telegram-вход и выход, доступ по ролям, поиск в «Новой
сделке» и BestChange, открытие карточки клиента и таблицу расходов. В выводе
`check` сверить `outbox`, `api_http`, `frontend_http` и `oidc_http`, а в журнале
backend — получение обновлений ботом и сводку `Message archive health`, если
архив включён. Запись сделок и расходов и пилот экспорта переписки проводятся
в изолированном контуре. Предыдущие каталоги, backup и его manifest сохранить.

Код выхода `2` у `check` означает, что в JSON есть проверки со статусом
`fail`. По отдельности сверить доступность API и HTTPS, настройки и outbox.
Если `usdt_wallet` не проходит, администратор с правом управления настройками
задаёт рабочий адрес USDT TRC-20 командой `/setwallet <адрес>` в админском чате.
Команда сохраняет адрес в Thai БД, после неё можно сразу повторить `check`.

Для проверки очереди достаточно сводки по проблемным событиям. Команда ниже
выполняет только чтение и выводит типы, статусы и число попыток.

```bash
runuser -u postgres -- psql -X -p 5433 -d thai_crm -v ON_ERROR_STOP=1 <<'SQL'
BEGIN READ ONLY;
SELECT event_type, status, count(*) AS events, max(attempts) AS max_attempts
FROM telegram_outbox
WHERE status = 'failed'
   OR (status = 'processing' AND lease_until <= clock_timestamp())
   OR (status = 'pending' AND available_at < clock_timestamp() - interval '5 minutes')
GROUP BY event_type, status
ORDER BY event_type, status;
COMMIT;
SQL
```

## Откат

Откат возвращает обе ссылки из сохранённого состояния. Текущая БД используется
с сохранением всех операций после backup. Применять при подтверждённой
совместимости `thai_0027` и прежней сборки.

```bash
bash <<'BASH'
set -euo pipefail
. /root/skyex-thai-384b422.state
test -x "$old_backend/.venv/bin/python"
test -f "$old_frontend/.next/standalone/server.js"
systemctl stop skyex-thai-frontend skyex-thai-backend
rollback_id="$(date -u +%Y%m%dT%H%M%SZ)-rollback"
ln -s "$old_frontend" "$base/frontend-current.$rollback_id"
mv -Tf "$base/frontend-current.$rollback_id" "$base/frontend-current"
ln -s "$old_backend" "$base/current.$rollback_id"
mv -Tf "$base/current.$rollback_id" "$base/current"
systemctl start skyex-thai-backend
curl --noproxy '*' --fail --retry 20 --retry-connrefused --retry-delay 1 --max-time 10 \
  http://127.0.0.1:8001/health/ready
systemctl start skyex-thai-frontend
systemctl is-active skyex-thai-backend skyex-thai-frontend
curl --noproxy '*' --fail --retry 8 --retry-connrefused --retry-delay 1 --max-time 15 \
  -o /dev/null https://thai-crm.work.gd/login
BASH
```
