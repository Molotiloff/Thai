from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from db_asyncpg.schema_identity import require_thai_database_url
from services.aml.getblock_settings import GetBlockSettings


@dataclass(frozen=True)
class Config:
    database_url: str = field(repr=False)
    jwt_secret: str = field(repr=False)
    bot_token: str | None = field(default=None, repr=False)
    admin_ids: tuple[int, ...] = ()
    api_host: str = "127.0.0.1"
    api_port: int = 8001
    cors_origins: tuple[str, ...] = ()
    operations_chat_id: int | None = None
    tronscan_api_key: str | None = field(default=None, repr=False)
    telegram_oidc_client_id: str | None = None
    telegram_oidc_client_secret: str | None = field(default=None, repr=False)
    frontend_origin: str | None = None
    usdt_wallet: str | None = None
    admin_chat_id: int | None = None
    converter_api_base_url: str | None = None
    converter_api_token: str | None = field(default=None,repr=False)
    message_archive_enabled: bool = True
    message_archive_media_dir: str = '.runtime/message_archive/media'
    message_archive_temp_dir: str = '.runtime/message_archive/exports'
    getblock: GetBlockSettings | None = field(default=None,repr=False)

    api_enabled: bool = True
    message_archive_download_workers: int = 2
    message_archive_download_queue_size: int = 500
    message_archive_export_workers: int = 1
    message_archive_export_part_bytes: int = 40*1024*1024
    cash_chat_map: dict[str,int] = field(default_factory=dict)
    city_schedule_chats: dict[str,int] = field(default_factory=dict)
    city_cash_chat_map: dict[str,int] = field(default_factory=dict)
    best_change_chat_id: int | None = None
    daniyar_chat_id: int | None = None

    @property
    def request_chat_id(self):
        return self.operations_chat_id

    @property
    def crm_jwt_secret(self):
        return self.jwt_secret

    @property
    def api_cors_origins(self):
        return self.cors_origins

    @property
    def database_schema(self):
        return 'thai'

    @classmethod
    def from_env(cls,path='.env.thai'):
        return cls.load(path)

    @classmethod
    def load(cls, path=".env.thai"):
        if not Path(path).is_file():
            raise RuntimeError("Отдельный .env.thai обязателен")
        env = dict(dotenv_values(path, interpolate=False))
        # Keep the original CRM env keys; accept previously configured branch aliases.
        aliases = {
            'BOT_TOKEN':'THAI_BOT_TOKEN','ADMIN_IDS':'THAI_ADMIN_IDS',
            'ADMIN_CHAT_ID':'THAI_ADMIN_CHAT_ID','REQUEST_CHAT_ID':'THAI_OPERATIONS_CHAT_ID',
            'TRONSCAN_API_KEY':'THAI_TRONSCAN_API_KEY','USDT_WALLET':'THAI_USDT_WALLET',
            'TELEGRAM_OIDC_CLIENT_ID':'THAI_TELEGRAM_OIDC_CLIENT_ID',
            'TELEGRAM_OIDC_CLIENT_SECRET':'THAI_TELEGRAM_OIDC_CLIENT_SECRET',
            'CRM_FRONTEND_ORIGIN':'THAI_FRONTEND_ORIGIN',
            'CONVERTER_API_BASE_URL':'THAI_CONVERTER_API_BASE_URL','CONVERTER_API_TOKEN':'THAI_CONVERTER_API_TOKEN',
            'MESSAGE_ARCHIVE_ENABLED':'THAI_MESSAGE_ARCHIVE_ENABLED',
            'MESSAGE_ARCHIVE_MEDIA_DIR':'THAI_MESSAGE_ARCHIVE_MEDIA_DIR',
            'MESSAGE_ARCHIVE_TEMP_DIR':'THAI_MESSAGE_ARCHIVE_TEMP_DIR',
            'GETBLOCK_REPORTS_DIR':'THAI_AML_REPORTS_DIR','CRM_API_CORS_ORIGINS':'CRM_CORS_ORIGINS',
        }
        for canonical, alias in aliases.items():
            if not env.get(canonical) and env.get(alias):
                env[canonical]=env[alias]

        if (
            env.get("CRM_DATABASE_SCHEMA") != "thai"
            or not env.get("DATABASE_URL")
            or len(env.get("CRM_JWT_SECRET") or "") < 32
        ):
            raise RuntimeError("Нужны Thai schema, DATABASE_URL и отдельный JWT-secret")
        require_thai_database_url(env['DATABASE_URL'])
        if bool(env.get('TELEGRAM_OIDC_CLIENT_ID')) != bool(env.get('TELEGRAM_OIDC_CLIENT_SECRET')):
            raise RuntimeError('TELEGRAM_OIDC_CLIENT_ID и TELEGRAM_OIDC_CLIENT_SECRET должны быть заданы вместе')

        def chat_map(key):
            result={}
            for entry in (env.get(key) or '').split(','):
                if not entry.strip():
                    continue
                name,separator,chat_id=entry.partition(':')
                if not separator or not name.strip() or name.strip() in result:
                    raise RuntimeError(f'Некорректная настройка {key}')
                result[name.strip()]=int(chat_id.strip())
            return result
        getblock=None
        business_chats={key:int(env[key]) if env.get(key) else None for key in ('BEST_CHANGE_CHAT_ID','DANIYAR_CHAT')}
        if any(value==0 for value in business_chats.values()):
            raise RuntimeError('BEST_CHANGE_CHAT_ID и DANIYAR_CHAT должны быть ненулевыми Telegram ID')
        if business_chats['BEST_CHANGE_CHAT_ID'] is not None and business_chats['BEST_CHANGE_CHAT_ID']==business_chats['DANIYAR_CHAT']:
            raise RuntimeError('BestChange и Баланс Данияр должны использовать разные чаты')
        if env.get('GETBLOCK_IDENTITY') and env.get('GETBLOCK_PASSWORD'):
            getblock=GetBlockSettings(identity=env['GETBLOCK_IDENTITY'],password=env['GETBLOCK_PASSWORD'],
                lang=env.get('GETBLOCK_LANG') or 'en',user_id=env.get('GETBLOCK_USER_ID') or '',
                currency_code=env.get('GETBLOCK_CURRENCY_CODE') or 'TRX',token_id=env.get('GETBLOCK_TOKEN_ID') or '9',
                aml_provider=env.get('GETBLOCK_AML_PROVIDER') or '2',direction=env.get('GETBLOCK_DIRECTION') or '2',
                source=env.get('GETBLOCK_SOURCE') or '1',type_=env.get('GETBLOCK_TYPE') or '0',
                reports_dir=env.get('GETBLOCK_REPORTS_DIR') or '.runtime/aml_reports')
        return cls(
            env["DATABASE_URL"],
            env["CRM_JWT_SECRET"],
            env.get("BOT_TOKEN") or None,
            tuple(
                int(value.strip())
                for value in (env.get("ADMIN_IDS") or "").split(",")
                if value.strip()
            ),
            env.get("CRM_API_HOST") or "127.0.0.1",
            int(env.get("CRM_API_PORT") or 8001),
            tuple(
                value.strip()
                for value in (env.get("CRM_API_CORS_ORIGINS") or "").split(",")
                if value.strip()
            ),
            int(env["REQUEST_CHAT_ID"]) if env.get("REQUEST_CHAT_ID") else None,
            env.get("TRONSCAN_API_KEY") or None,
            env.get("TELEGRAM_OIDC_CLIENT_ID") or None,
            env.get("TELEGRAM_OIDC_CLIENT_SECRET") or None,
            env.get("CRM_FRONTEND_ORIGIN") or None,
            env.get("USDT_WALLET") or None,
            int(env['ADMIN_CHAT_ID']) if env.get('ADMIN_CHAT_ID') else None,
            env.get('CONVERTER_API_BASE_URL') or None,
            env.get('CONVERTER_API_TOKEN') or None,
            (env.get('MESSAGE_ARCHIVE_ENABLED') or 'true').lower() in ('1','true','yes'),
            env.get('MESSAGE_ARCHIVE_MEDIA_DIR') or '.runtime/message_archive/media',
            env.get('MESSAGE_ARCHIVE_TEMP_DIR') or '.runtime/message_archive/exports',
            getblock,
            (env.get('CRM_API_ENABLED') or 'true').lower() in ('1','true','yes','on'),
            max(1,int(env.get('MESSAGE_ARCHIVE_DOWNLOAD_WORKERS') or 2)),
            max(1,int(env.get('MESSAGE_ARCHIVE_DOWNLOAD_QUEUE_SIZE') or 500)),
            max(1,int(env.get('MESSAGE_ARCHIVE_EXPORT_WORKERS') or 1)),
            max(1024*1024,int(env.get('MESSAGE_ARCHIVE_EXPORT_PART_BYTES') or 40*1024*1024)),
            chat_map('CASH_CHAT_ID'),chat_map('CITY_SCHEDULE_CHATS'),chat_map('CITY_CASH_CHAT_IDS'),
            business_chats['BEST_CHANGE_CHAT_ID'],business_chats['DANIYAR_CHAT'],
        )
