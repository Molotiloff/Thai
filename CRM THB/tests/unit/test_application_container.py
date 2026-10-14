from unittest.mock import MagicMock

from aiogram import Bot, Dispatcher

from api.app import create_api_app
from app.container import ApplicationContainer
from app.setup_handlers import setup_handlers
from config import Config


async def test_container_builds_shared_api_and_telegram_graph():
    config=Config(database_url='postgresql://unused@localhost:5433/thai',jwt_secret='x'*32,
        bot_token='123456:TEST',message_archive_enabled=False)
    pool=MagicMock()
    container=ApplicationContainer.build(pool,config=config)
    api=create_api_app(config,container=container)
    bot=Bot(token=config.bot_token)
    dispatcher=Dispatcher()
    runtime=setup_handlers(dp=dispatcher,bot=bot,container=container)
    try:
        assert api.state.container is container and container.pool is pool
        assert container.config is config
        assert runtime.tg_outbox_worker._delivery._app is container
        assert runtime.payment_watch_poller._service is container.payment_watches
        assert runtime.aml_queue_service is None and runtime.message_archive is None
        assert len(dispatcher.sub_routers)==1
    finally:
        await runtime.stop()
        await bot.session.close()
