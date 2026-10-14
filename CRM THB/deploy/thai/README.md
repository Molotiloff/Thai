# Шаблоны запуска тайской CRM

Основная инструкция: [PRODUCTION_DEPLOY.md](../PRODUCTION_DEPLOY.md).
Домен: **https://thai-crm.work.gd**. Backend — 127.0.0.1:8001,
frontend — 127.0.0.1:3001, отдельный PostgreSQL — 127.0.0.1:5433.
Новый пользователь БД — `thai_crm_app`, база — `thai_crm`.

В этой папке находятся единственные актуальные systemd/Nginx-шаблоны проекта.
Старые российские шаблоны удалены из этой копии, чтобы исключить установку
сервисов с путями `/root/CRM` и портами российского контура.
Существующие сервисы РФ на сервере не менять.

- `skyex-thai-backend.service` запускает существующий `main.py`:
  API и Telegram worker используют общий `BotApp.run()` и setup handlers.
- Backend читает `.env.thai` из release-каталога: это ссылка на
  `/opt/skyex-thai/shared/.env.thai`. EnvironmentFile ему не нужен.
- `skyex-thai-frontend.service` запускает Next standalone, читает публичные
  настройки из `/opt/skyex-thai/shared/frontend.env`.
- `frontend.env.example` содержит origin и публичный OIDC Client ID.
  Backend-секреты сюда не копировать.
- `nginx.conf.example` — итоговый HTTPS vhost. До его установки получить
  сертификат по временному HTTP vhost из основной инструкции.

Итоговая схема и результаты проверки: [SCHEMA_VERIFICATION.md](SCHEMA_VERIFICATION.md).
Шаблоны подготовлены; установка, миграции и запуск на сервере не выполнялись.
