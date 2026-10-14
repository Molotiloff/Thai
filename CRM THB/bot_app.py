"""Run the copied CRM application using the branch configuration and database."""
import asyncio
import logging

import uvicorn
from aiogram import Bot, Dispatcher

from api.app import create_api_app
from app.container import ApplicationContainer
from app.lifecycle import AsyncServerLifecycleAdapter
from app.runtime import BotRuntimeServices
from app.setup_handlers import setup_handlers
from config import Config
from db_asyncpg.migrator import AlembicMigrator
from db_asyncpg.pool import close_pool, create_pool
from db_asyncpg.schema_identity import require_thai_database_url
from middlewares.dedup import DedupMiddleware
from observability import configure_logging


async def bootstrap_admins(pool, admin_ids):
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute('SELECT pg_advisory_xact_lock(736501)')
        if await connection.fetchval('SELECT EXISTS(SELECT 1 FROM users WHERE is_active)'):
            return
        for telegram_id in admin_ids:
            actor=await connection.fetchval(
                "INSERT INTO users(display_name,telegram_user_id) VALUES ('Administrator',$1) RETURNING id",telegram_id)
            await connection.execute("INSERT INTO user_roles VALUES ($1,'admin')",actor)


class BotApp:
    def __init__(self, config: Config, *, api_only=False):
        self.config=config
        self.bot=Bot(token=config.bot_token) if config.bot_token and not api_only else None
        self.container=None
        self.services=None
        self.dp=Dispatcher()
        self.dp.startup.register(self._on_startup)
        self.dp.shutdown.register(self._on_shutdown)
        self.dp.message.middleware(DedupMiddleware())
        self.dp.callback_query.middleware(DedupMiddleware())

    async def _on_startup(self):
        if self.services is None:
            raise RuntimeError('Bot services are not initialized')
        await self.services.start(bot=self.bot,config=self.config)

    async def _on_shutdown(self):
        if self.services is not None:
            await self.services.stop()

    def _bootstrap(self,pool):
        self.container=ApplicationContainer.build(pool,config=self.config)
        self.services=setup_handlers(dp=self.dp,bot=self.bot,container=self.container) if self.bot else BotRuntimeServices()

    async def run(self):
        # Validate before migration/pool creation: the Russian database is never a target.
        require_thai_database_url(self.config.database_url)
        pool=None
        api_lifecycle=None
        try:
            await AlembicMigrator(self.config.database_url,schema='thai').upgrade_to_head()
            pool=await create_pool(self.config.database_url)
            await bootstrap_admins(pool,self.config.admin_ids)
            self._bootstrap(pool)
            await self.container.initialize_business_chats()
            server=self._create_api_server()
            if server is not None:
                api_lifecycle=AsyncServerLifecycleAdapter(server,task_name='crm-api')
                await api_lifecycle.start()
                logging.info('CRM API is starting (host=%s, port=%s)',self.config.api_host,self.config.api_port)
            if self.bot is not None:
                allowed_updates=self.dp.resolve_used_update_types()
                if self.config.message_archive_enabled:
                    allowed_updates=sorted(set(allowed_updates)|{'message','edited_message','channel_post',
                        'edited_channel_post','business_message','edited_business_message'})
                await self.dp.start_polling(self.bot,allowed_updates=allowed_updates,close_bot_session=False)
            elif api_lifecycle is not None:
                await api_lifecycle.wait()
            else:
                raise RuntimeError('Configure BOT_TOKEN or enable CRM_API_ENABLED in .env.thai')
        finally:
            try:
                await self._on_shutdown()
            finally:
                try:
                    if api_lifecycle is not None:
                        await api_lifecycle.stop()
                finally:
                    try:
                        if self.bot is not None:
                            await self.bot.session.close()
                    finally:
                        if pool is not None:
                            await close_pool(pool)

    def _create_api_server(self):
        if not self.config.api_enabled:
            return None
        if self.container is None:
            raise RuntimeError('Application container is not initialized')
        app=create_api_app(self.config,container=self.container)
        return uvicorn.Server(uvicorn.Config(app,host=self.config.api_host,port=self.config.api_port,
            log_config=None,access_log=False))


def run_app(*,api_only=False):
    configure_logging()
    asyncio.run(BotApp(Config.from_env('.env.thai'),api_only=api_only).run())


if __name__=='__main__':
    run_app()
