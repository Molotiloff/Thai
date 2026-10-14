"""Register the copied CRM handlers against the branch's shared services."""
import re
import shlex
from contextlib import suppress

from aiogram import F, Router
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.runtime import BotRuntimeServices
from app.thai_russian_commands import CASH, FX_CASH, RECEIVE
from app.thai_telegram import ThaiTelegramCommands, ThaiTelegramDelivery
from domain.finance.models import FinanceError
from services.finance.outbox import DeliveryError
from services.payment_watch.service import PaymentMonitor
from services.payment_watch.tronscan_gateway import TronscanGateway, TronscanSettings


def setup_message_archive(dispatcher,bot,container,config):
    """Compose the existing archive stack against the independent database."""
    from handlers.message_archive import MessageArchiveHandler
    from middlewares.message_archive import (
        IncomingMessageArchiveMiddleware,
        OutgoingMessageArchiveMiddleware,
    )
    from services.message_archive import (
        ArchiveRuntimeMetrics,
        LocalMediaStorage,
        MediaDownloadService,
        MessageArchiveLifecycle,
        MessageArchiveMonitor,
        MessageArchiveService,
        MessageExportService,
    )
    from telegram_adapters.message_archive import AiogramArchiveMediaSource
    repository=container.message_archive
    storage=LocalMediaStorage(config.message_archive_media_dir)
    media=MediaDownloadService(repo=repository,storage=storage,telegram_media=AiogramArchiveMediaSource(bot),
        workers=config.message_archive_download_workers,queue_size=config.message_archive_download_queue_size)
    metrics=ArchiveRuntimeMetrics()
    service=MessageArchiveService(repo=repository,attachment_scheduler=media,metrics=metrics)
    incoming=IncomingMessageArchiveMiddleware(service)
    for name in ('message','edited_message','channel_post','edited_channel_post','business_message','edited_business_message'):
        dispatcher.observers[name].outer_middleware(incoming)
    bot.session.middleware(OutgoingMessageArchiveMiddleware(service))
    handler=MessageArchiveHandler(bot=bot,repo=repository,client_repo=container.client_queries,
        manager_repo=container.client_queries,admin_chat_id=config.admin_chat_id,
        authorize=container.client_queries.is_manager,export_workers=config.message_archive_export_workers,
        export_service=MessageExportService(repo=repository,storage=storage,temp_dir=config.message_archive_temp_dir,
            part_size=config.message_archive_export_part_bytes,timezone='Asia/Bangkok'))
    lifecycle=MessageArchiveLifecycle(media=media,exports=handler,
        monitor=MessageArchiveMonitor(repo=repository,runtime=metrics,media_queue=media))
    return handler,lifecycle


def setup_aml(container,config,gateway=None):
    if config.getblock is None:
        return None,None
    from handlers.aml import AMLHandler
    from services.aml import AMLQueueService, AMLService, ThreadedAMLChecker
    queue=AMLQueueService(checker=ThreadedAMLChecker(AMLService(settings=config.getblock)),
        tron_balance_provider=gateway)
    return AMLHandler(container.client_queries,aml_queue_service=queue),queue


def setup_handlers(*, dp, bot, container, ignore_chat_ids=None):
    del ignore_chat_ids
    config=container.config
    if config is None:
        raise RuntimeError('ApplicationContainer requires configuration for Telegram startup')
    services=BotRuntimeServices()
    services.payment_watch_gateway=TronscanGateway(settings=TronscanSettings(api_key=config.tronscan_api_key,minimum_confirmations=2))
    services.payment_watch_poller=PaymentMonitor(container.payment_watches,services.payment_watch_gateway)
    archive_handler=None
    if config.message_archive_enabled:
        archive_handler,services.message_archive=setup_message_archive(dp,bot,container,config)
    aml_handler,services.aml_queue_service=setup_aml(container,config,services.payment_watch_gateway)
    commands=ThaiTelegramCommands(container,config)
    services.tg_outbox_worker=container.outbox_dispatcher(ThaiTelegramDelivery(container,bot,config.request_chat_id))
    dp.include_router(create_router(commands,archive_handler=archive_handler,aml_handler=aml_handler))
    return services


def create_router(commands, *, archive_handler=None, aml_handler=None):
    router = Router(name="thai-crm")
    from handlers.act import ActHandler
    from handlers.broadcast_all import BroadcastAllHandler
    from handlers.managers import ManagersHandler
    from handlers.xe import XEHandler
    from services.broadcast import BroadcastService
    from services.xe_api import ConverterAPIService
    from telegram_adapters.broadcast_presenter import AiogramBroadcastPresenter
    from telegram_adapters.broadcast_session_store import AiogramBroadcastSessionStore
    container,config=commands._app,commands._config
    converter=ConverterAPIService(base_url=config.converter_api_base_url,api_token=config.converter_api_token) if (
        config.converter_api_base_url and config.converter_api_token) else None
    commands.xe=XEHandler(repo=container.client_queries,converter_service=converter)
    request_chat_id = config.operations_chat_id or config.admin_chat_id
    commands.act = ActHandler(repo=container.client_queries, act_counter_service=container.act_counter,
        request_chat_ids=[request_chat_id] if request_chat_id is not None else [],
        accounting_authorize=commands.authorize)
    commands.managers=ManagersHandler(container.manager_admin,config.admin_chat_id,authorize=commands.authorize)
    commands.broadcast=BroadcastAllHandler(container.client_queries,
        broadcast_service=BroadcastService(repo=container.client_queries,
            excluded_chat_ids={value for value in (config.admin_chat_id,config.operations_chat_id) if value is not None}),
        session_store=AiogramBroadcastSessionStore(),preview_builder=AiogramBroadcastPresenter(),
        admin_chat_ids={config.admin_chat_id} if config.admin_chat_id is not None else set(),strict_staff_roles=True)

    from handlers.accept_short import AcceptShortHandler
    from handlers.cash_requests import CashRequestsHandler
    CashRequestsHandler.register_accounting(router, commands)
    AcceptShortHandler.register_accounting(router, commands)
    from handlers.balances_clients import ClientsBalancesHandler
    from handlers.best_change import BestChangeHandler
    from handlers.clients import ClientsHandler
    from handlers.payment_watch import PaymentWatchHandler
    from handlers.wallets import WalletsHandler
    PaymentWatchHandler.register_accounting(router,commands)
    WalletsHandler.register_accounting(router,commands)
    from handlers.calc import CalcHandler, _cmd_calc, _slash_calc
    from handlers.debug import cmd_whoami
    router.include_router(CalcHandler().router)
    router.include_router(commands.broadcast.router)
    if archive_handler is not None:
        router.include_router(archive_handler.router)

    @router.message(F.text.startswith('/'))
    async def message_handler(message: Message):
        if message.from_user is None or not (message.text or "").startswith("/"):
            return
        if commands.broadcast._is_broadcast_reply(message):
            await commands.broadcast._handle_broadcast_reply(message)
            return
        command = ""
        try:
            words = shlex.split(message.text)
            command = words[0].split("@")[0].lower()
            if command=='/whoami':
                await cmd_whoami(message)
                return
            if command=='/xe':
                await commands.xe._cmd_xe(message)
                return
            if command=='/mgr':
                await commands.managers._cmd_mgr(message)
                return
            if command=='/всем':
                await commands.broadcast._cmd_all(message)
                return
            if command=='/амл':
                if aml_handler is None:
                    await message.answer('AML не настроен: проверьте GETBLOCK_IDENTITY и GETBLOCK_PASSWORD в .env.thai.')
                else:
                    await aml_handler._cmd_aml(message)
                return
            if command=='/акт':
                if not commands.act.request_chat_ids:
                    await message.answer('Укажите рабочий или админский чат в .env.thai для служебного счёта /акт.')
                else:
                    await commands.act._cmd_act(message)
                return
            if command=='/calc':
                await _cmd_calc(message)
                return
            if re.match(r'^/[+\-0-9(]',message.text):
                await _slash_calc(message)
                return
            if command=='/сообщение':
                if archive_handler is None:
                    await message.answer('Архивирование отключено в этом процессе.')
                else:
                    await archive_handler._command(message)
                return
            if await PaymentWatchHandler.accounting_command(commands,message,command):
                return
            if await BestChangeHandler.accounting_command(commands,message,command):
                return
            if await ClientsHandler.accounting_command(commands,message,command):
                return
            if await ClientsBalancesHandler.accounting_command(commands,message,command):
                return
            if await WalletsHandler.accounting_wallet_command(commands,message,command):
                return
            if command=="/перевод":
                await WalletsHandler.accounting_transfer(commands,message)
                return
            if command=="/отмена" and await WalletsHandler.accounting_transfer(commands,message,cancel=True):
                return
            if command in CASH or command in FX_CASH:
                await CashRequestsHandler.accounting_request(commands, message)
                return
            if command in RECEIVE:
                await AcceptShortHandler.accounting_exchange(commands,message)
                return
            if command=='/отмена' and await AcceptShortHandler.accounting_cancel_reply(commands,message):
                return
            text = await commands.execute(
                telegram_user_id=message.from_user.id,
                chat_id=message.chat.id,
                message_id=message.message_id,
                text=message.text,
                effective_at=message.date,
                private=message.chat.type == "private",
                chat_name=message.chat.title or message.chat.full_name,
                actor_name=message.from_user.full_name,
            )
        except FinanceError as error:
            text = str(error)
        except (ValueError, IndexError):
            text = "Некорректные аргументы; /помощь"
        if text.startswith('Заявка Б-') and ('проведена: операция' in text or 'сторона подтверждена' in text):
            request_id = shlex.split(message.text)[-1]
            # The journal and request committed together; the outbox updates the cards later.
            with suppress(DeliveryError):
                await commands.deliver_cash_request(message.bot, commands._app.cash_requests.deal_id(request_id))
            await message.answer(text)
            return
        if command == "/кнопки":
            markup = ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text="/помоги")],
                [KeyboardButton(text="/дай"), KeyboardButton(text="/кош")],
                [KeyboardButton(text="/help"), KeyboardButton(text="/info")],
            ], resize_keyboard=True, input_field_placeholder="Выберите команду")
            await message.answer(text, reply_markup=markup)
        elif command in ("/start", "/помоги", "/скрыть"):
            await message.answer(text, reply_markup=ReplyKeyboardRemove())
        else:
            await message.answer(text, **({"parse_mode":"HTML"} if command in ("/кошелек", "/кошелёк", "/дай") and text.startswith("<code>") else {}))

    return router
