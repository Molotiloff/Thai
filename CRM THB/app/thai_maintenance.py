"""Offline Thai database backup/restore. Never loads the Russian environment."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import asyncpg
import httpx
from dotenv import dotenv_values

from config import Config
from db_asyncpg.migrator import AlembicMigrator
from db_asyncpg.schema_identity import (
    is_thai_runtime_ready,
    require_thai_database_url,
    thai_runtime_status,
)
from domain.tron_wallet import is_probable_tron_wallet


def _release_url(value):
    try:
        parsed=urlsplit(value or '')
        _ = parsed.port
        return parsed
    except ValueError:
        return urlsplit('')


class ThaiDatabaseMaintenance:
    def __init__(self, database_url: str):
        parsed = require_thai_database_url(database_url)
        self._url, self._parsed = database_url, parsed

    async def check(self, config, *, frontend_env: Path | None = None, expect_empty=False,frontend_url=None):
        """Read-only release evidence; no tokens, DSN, balances or client names in output."""
        checks=[]
        def add(code,passed,detail,*,warning=False):
            checks.append({'code':code,'status':'pass' if passed else 'warn' if warning else 'fail','detail':detail})
        add('bot',bool(config.bot_token),'Отдельный Telegram-токен задан')
        add('oidc',all((config.telegram_oidc_client_id,config.telegram_oidc_client_secret)),
            'OIDC Client ID и Secret заданы')
        origin=_release_url(config.frontend_origin)
        valid_origin=bool(origin.scheme=='https' and origin.hostname and origin.path in ('','/')
            and not origin.username and not origin.password and not origin.query and not origin.fragment)
        add('public_origin',valid_origin,'Задан публичный HTTPS origin без пути и параметров')
        add('operations_chat',config.operations_chat_id is not None,
            'Отдельный рабочий чат задан; без него применяется исходный чат заявки',warning=True)
        if frontend_env is not None:
            frontend=dotenv_values(frontend_env,interpolate=False) if frontend_env.is_file() else {}
            add('frontend_env',frontend_env.is_file(),'Файл конфигурации frontend существует')
            add('origin_match',bool(valid_origin and (frontend.get('THAI_FRONTEND_ORIGIN') or '').rstrip('/')==config.frontend_origin.rstrip('/')),
                'Backend и frontend используют один публичный origin')
            add('oidc_client_match',bool(config.telegram_oidc_client_id and frontend.get('THAI_TELEGRAM_OIDC_CLIENT_ID')==config.telegram_oidc_client_id),
                'OIDC Client ID совпадает в обоих процессах')
            base=_release_url(frontend.get('THAI_API_BASE_URL'))
            add('frontend_api',bool(base.scheme in ('http','https') and base.hostname and base.path.rstrip('/')=='/api/v1'
                and not base.username and not base.password and not base.query and not base.fragment),
                'Frontend настроен на финансовый API /api/v1')
            if base.hostname in ('localhost','127.0.0.1','::1'):
                add('api_port_match',(base.port or (443 if base.scheme=='https' else 80))==config.api_port,
                    'Локальный frontend обращается к настроенному порту API')
            add('frontend_secrets',not any(frontend.get(key) for key in ('THAI_BOT_TOKEN','THAI_TELEGRAM_OIDC_CLIENT_SECRET','CRM_JWT_SECRET')),
                'Backend-секреты отсутствуют в конфигурации frontend')
        else:
            add('frontend_env',False,'Конфигурация frontend не проверена: укажите --frontend-env',warning=True)
        db=None
        try:
            db=await asyncpg.connect(self._url,timeout=5,command_timeout=10,
                server_settings={'default_transaction_read_only':'on'})
            async with db.transaction(isolation='repeatable_read',readonly=True):
                status=await thai_runtime_status(db)
                expected=AlembicMigrator(self._url,schema='thai').head_revision()
                add('database',is_thai_runtime_ready(status,expected),f'Изолированная Thai/USDT/THB/Bangkok, ревизия {expected}')
                if not is_thai_runtime_ready(status,expected):
                    return self._check_result(checks)
                instruments=await db.fetchval('SELECT count(*) FROM instruments WHERE is_active')
                add('instruments',instruments==8,'Восемь активных инструментов')
                admins=await db.fetchval("""
                    SELECT count(DISTINCT u.id) FROM users u JOIN user_roles r ON r.user_id=u.id
                    WHERE u.is_active AND r.role IN ('admin','owner') AND u.telegram_user_id=ANY($1::bigint[])
                """,list(config.admin_ids))
                add('administrators',admins>0,'Есть активный администратор/владелец из THAI_ADMIN_IDS')
                bindings=await db.fetchval("""
                    SELECT count(*) FROM business_client_bindings b JOIN clients c ON c.id=b.client_id
                    WHERE c.is_active AND EXISTS(SELECT 1 FROM client_chat_links l WHERE l.client_id=c.id AND l.is_active)
                """)
                add('business_chats',bindings==2,'BestChange и Баланс Данияр привязаны к активным клиентским чатам')
                wallet=await db.fetchval("SELECT value #>> '{}' FROM crm_settings WHERE key='usdt_wallet'")
                add('usdt_wallet',is_probable_tron_wallet(wallet or config.usdt_wallet or ''),'Кошелёк отправки USDT TRON задан')
                drift=await db.fetchval("""
                    SELECT count(*) FROM ledger_accounts a JOIN posted_account_balances j ON j.account_id=a.id
                    LEFT JOIN account_balances b ON b.account_id=a.id
                    WHERE b.account_id IS NULL OR b.debit_signed_native<>j.balance*CASE a.normal_side WHEN 'debit' THEN 1 ELSE -1 END
                        OR b.debit_signed_usdt<>j.carrying_usdt*CASE a.normal_side WHEN 'debit' THEN 1 ELSE -1 END
                        OR b.debit_signed_unvalued<>j.unvalued_native*CASE a.normal_side WHEN 'debit' THEN 1 ELSE -1 END
                """)
                add('account_projections',drift==0,'Проекции счетов совпадают с неизменяемым журналом')
                inventory=await db.fetchval("""
                    SELECT count(*) FROM cash_accounts c JOIN posted_account_balances j ON j.account_id=c.ledger_account_id
                    LEFT JOIN inventory_balances i ON i.instrument_code=c.instrument_code
                    WHERE i.instrument_code IS NULL OR i.quantity<>j.balance OR i.cost_usdt<>j.carrying_usdt
                        OR i.unvalued_quantity<>j.unvalued_native
                """)
                add('cash_projections',inventory==0,'Количество и себестоимость кассы совпадают с журналом')
                broken=await db.fetchval("""
                    SELECT count(*) FROM (SELECT l.operation_id FROM journal_lines l JOIN financial_operations o ON o.id=l.operation_id
                    WHERE o.status='posted' GROUP BY l.operation_id
                    HAVING sum(CASE l.side WHEN 'debit' THEN l.amount_usdt ELSE -l.amount_usdt END)<>0) t
                """)
                add('journal',broken==0,'Проведённые денежные операции сбалансированы в USDT')
                queue=await db.fetchval("""
                    SELECT count(*) FROM telegram_outbox WHERE status='failed'
                        OR (status='processing' AND lease_until<=clock_timestamp())
                        OR (status='pending' AND available_at<clock_timestamp()-interval '5 minutes')
                """)
                add('outbox',queue==0,'Нет ошибок доставки, просроченных lease и давних pending-событий')
                if expect_empty:
                    used=await db.fetchval("""SELECT (SELECT count(*) FROM clients)+(SELECT count(*) FROM financial_operations)
                        +(SELECT count(*) FROM journal_lines)+(SELECT count(*) FROM capital_contributions)""")
                    add('empty_release',used==0,'Нулевой старт: нет клиентов, операций, журнала и вкладов')
        except (asyncpg.PostgresError,OSError,TimeoutError):
            add('database',False,'БД недоступна либо не содержит ожидаемой Thai-схемы; реквизиты скрыты')
        finally:
            if db is not None:
                await db.close()
        if frontend_url is not None:
            await self._check_http(config,frontend_url,add,origin if valid_origin else None)
        return self._check_result(checks)

    async def _check_http(self,config,frontend_url,add,public_origin):
        frontend=_release_url(frontend_url)
        valid=(frontend.scheme in ('http','https') and frontend.hostname in ('localhost','127.0.0.1','::1')
            and frontend.path in ('','/') and not frontend.username and not frontend.password
            and not frontend.query and not frontend.fragment)
        if not valid:
            add('http_target',False,'Для smoke-проверки нужен локальный frontend origin без пути/реквизитов')
            return
        async with httpx.AsyncClient(timeout=3,follow_redirects=False,trust_env=False) as client:
            for code,url in (('api_http',f'http://127.0.0.1:{config.api_port}/health/ready'),
                             ('frontend_http',frontend_url.rstrip('/')+'/login')):
                try:
                    result=await client.get(url)
                    healthy=result.status_code==200
                    if healthy and code=='api_http':
                        healthy=result.json()=={'status':'ready'}
                except (httpx.HTTPError,ValueError):
                    healthy=False
                add(code,healthy,'Локальный API готов' if code=='api_http' else 'Страница входа frontend доступна')
            if public_origin is None:
                add('oidc_http',False,'Публичный origin не задан корректно; начало OIDC не проверялось')
                return
            try:
                result=await client.get(frontend_url.rstrip('/')+'/api/auth/telegram/oidc/start',
                    headers={'Host':public_origin.netloc})
                redirect=_release_url(result.headers.get('location'))
                healthy=result.status_code in (302,303,307) and redirect.hostname=='oauth.telegram.org'
            except httpx.HTTPError:
                healthy=False
            add('oidc_http',healthy,'Frontend начинает вход через Telegram с настроенного публичного host')

    @staticmethod
    def _check_result(checks):
        return {'generated_at':datetime.now(UTC).isoformat(),
            'ready':not any(c['status']=='fail' for c in checks),'checks':checks}

    def _run(self, program, *options, stdout=None, database=None):
        p = self._parsed
        environment = {**os.environ, "PGPASSWORD": unquote(p.password or "")}
        command = [
            program,
            "--host",
            p.hostname,
            "--port",
            str(p.port),
            "--username",
            unquote(p.username),
            "--no-password",
            *options,
        ]
        if program == "pg_dump":
            command.append(database or unquote(p.path.strip("/")))
        else:
            command.extend(["--dbname", database])
        result = subprocess.run(
            command,
            env=environment,
            stdout=stdout or subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode:
            # Tool stderr can contain connection details; do not expose it or the DSN.
            raise RuntimeError(
                f"{program} failed (exit {result.returncode}); check PostgreSQL permissions/version"
            )

    @staticmethod
    def _digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    async def backup(self, output: Path):
        manifest = output.with_suffix(output.suffix + ".json")
        if output.exists() or manifest.exists():
            raise ValueError("Backup output already exists")
        db = await asyncpg.connect(self._url)
        created = False
        try:
            async with db.transaction(isolation="repeatable_read", readonly=True):
                if (
                    await db.fetchval("SELECT family FROM crm_schema_identity WHERE singleton")
                    != "thai"
                ):
                    raise ValueError("Source is not a Thai database")
                snapshot = await db.fetchval("SELECT pg_export_snapshot()")
                metadata = {
                    "family": "thai",
                    "format": 1,
                    "revision": await db.fetchval("SELECT version_num FROM alembic_version_thai"),
                    "operations": await db.fetchval("SELECT count(*) FROM financial_operations"),
                }
                with output.open("xb") as stream:
                    created = True
                    os.chmod(output, 0o600)
                    await asyncio.to_thread(
                        self._run,
                        "pg_dump",
                        "--format=custom",
                        "--snapshot",
                        snapshot,
                        stdout=stream,
                    )
            metadata["sha256"] = self._digest(output)
            with manifest.open("x") as stream:
                os.chmod(manifest, 0o600)
                json.dump(metadata, stream, indent=2)
            return metadata
        except BaseException:
            if created:
                output.unlink(missing_ok=True)
            raise
        finally:
            await db.close()

    async def restore(self, backup: Path, target_database: str):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", target_database):
            raise ValueError("Use a simple lowercase PostgreSQL database name")
        if target_database == unquote(self._parsed.path.strip("/")):
            raise ValueError("Restore into a separate empty database, never the source")
        metadata = json.loads(backup.with_suffix(backup.suffix + ".json").read_text())
        if (
            metadata.get("family") != "thai"
            or metadata.get("format") != 1
            or metadata.get("sha256") != self._digest(backup)
        ):
            raise ValueError("Backup manifest/checksum does not match")
        target_url = urlunsplit(self._parsed._replace(path="/" + target_database))
        db = await asyncpg.connect(target_url)
        try:
            count = await db.fetchval("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
                AND c.relkind IN ('r','p','v','m','S','f')""")
            if count:
                raise ValueError(
                    "Restore target must be empty; no --clean or destructive restore is supported"
                )
            await asyncio.to_thread(
                self._run,
                "pg_restore",
                "--single-transaction",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                str(backup),
                database=target_database,
            )
            if (
                await db.fetchval("SELECT family FROM crm_schema_identity WHERE singleton")
                != "thai"
            ):
                raise RuntimeError("Restored schema identity mismatch")
            if (
                await db.fetchval("SELECT version_num FROM alembic_version_thai")
                != metadata["revision"]
            ):
                raise RuntimeError("Restored revision mismatch")
            if (
                await db.fetchval("SELECT count(*) FROM financial_operations")
                != metadata["operations"]
            ):
                raise RuntimeError("Restored financial operation count mismatch")
            return metadata
        finally:
            await db.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=".env.thai")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--database", required=True)
    check=commands.add_parser('check')
    check.add_argument('--frontend-env',type=Path)
    check.add_argument('--expect-empty',action='store_true')
    check.add_argument('--frontend-url',help='Optional local frontend origin for HTTP smoke checks')
    check.add_argument('--output',type=Path,help='Write a sanitized JSON report')
    args = parser.parse_args()
    config=Config.load(args.config)
    maintenance = ThaiDatabaseMaintenance(config.database_url)
    if args.command == "backup":
        result = await maintenance.backup(args.output)
    elif args.command=='restore':
        result = await maintenance.restore(args.backup, args.database)
    else:
        result=await maintenance.check(config,frontend_env=args.frontend_env,expect_empty=args.expect_empty,
            frontend_url=args.frontend_url)
        if args.output:
            args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
            os.chmod(args.output,0o600)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.command=='check' and not result['ready']:
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
