from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from zoneinfo import ZoneInfo

from db_asyncpg.ports.administration import SettingsRepositoryPort
from db_asyncpg.ports.payment_watch import PaymentWatchRepositoryPort
from domain import SettlementReviewStatus
from domain.finance.models import FinanceError, InvalidPosting, decimal, exact
from domain.tron_wallet import is_probable_tron_wallet
from observability import NULL_METRICS, MetricsRecorder, measured_operation
from services.accounting.fulfillment_models import StartFulfillmentExecution
from services.accounting.fulfillment_service import (
    FulfillmentQueueError,
    FulfillmentQueueService,
)
from services.admin_client import USDT_WALLET_SETTING_KEY
from services.finance.posting import CallbackDocument
from services.lifecycle import ManagedTaskLifecycle
from services.metadata_commands import MetadataCommandService
from services.payment_watch.address_parser import extract_tron_address
from services.payment_watch.message_builder import PaymentWatchMessageBuilder
from services.payment_watch.models import (
    PaymentWatchNotification,
    PaymentWatchStarted,
    StartPaymentWatchCommand,
)
from services.payment_watch.settlement_models import ConfirmedTransfer
from services.payment_watch.settlement_service import DealSettlementService
from services.payment_watch.tronscan_gateway import TronscanGateway, TronscanGatewayError
from services.receipts import PaymentReceiptImageBuilder
from services.wallets.text_builder import WalletTextBuilder


class PaymentWatchError(Exception):
    pass


log = logging.getLogger("payment_watch")


class PaymentWatchService:
    def __init__(
        self,
        *,
        repo: PaymentWatchRepositoryPort | None = None,
        settings: SettingsRepositoryPort | None = None,
        tronscan_gateway: TronscanGateway | None = None,
        accounting_uow_factory=None, accounting_queries=None, wallet_queries=None, wallets=None, trading=None,
        settlement_service: DealSettlementService | None = None,
        fulfillment_queue_service: FulfillmentQueueService | None = None,
        timeout_seconds: int = 15 * 60,
        test_amount: Decimal = Decimal("1"),
        metrics: MetricsRecorder = NULL_METRICS,
    ) -> None:
        if accounting_uow_factory is not None and any(dependency is None for dependency in
                (accounting_queries,wallet_queries,wallets,trading)):
            raise ValueError('Ledger monitoring requires UoW, queries, wallet and trading services')
        self._accounting_factory = accounting_uow_factory
        self._commands = MetadataCommandService(accounting_uow_factory) if accounting_uow_factory else None
        self._accounting_queries,self._wallet_queries,self._wallets,self._trading = accounting_queries,wallet_queries,wallets,trading
        self.repo = repo
        self.settings = settings or cast(SettingsRepositoryPort, repo)
        self.tronscan_gateway = tronscan_gateway
        self.settlement_service = settlement_service
        self.fulfillment_queue_service = fulfillment_queue_service
        self.timeout_seconds = int(timeout_seconds)
        self.test_amount = Decimal(test_amount)
        self.builder = PaymentWatchMessageBuilder(minimum_confirmations=2 if accounting_uow_factory else 1,precision=6 if accounting_uow_factory else 3)
        self.receipt_builder = PaymentReceiptImageBuilder()
        self._metrics = metrics
        self._metrics.set_queue_size("payment_watch.active", 0)

    async def aclose(self) -> None:
        if self.tronscan_gateway is not None:
            await self.tronscan_gateway.aclose()

    async def start_watch(
        self,
        command: StartPaymentWatchCommand,
    ) -> PaymentWatchStarted:
        address = extract_tron_address(command.reply_text, command.reply_caption)
        if not address:
            raise PaymentWatchError("В сообщении не найден TRON-кошелёк.")

        our_address_raw = await self.settings.get_setting(USDT_WALLET_SETTING_KEY)
        our_address = (our_address_raw or "").strip()
        if not our_address:
            raise PaymentWatchError("Наш USDT-кошелёк не задан. Сначала используйте /setwallet.")
        if our_address == address:
            raise PaymentWatchError("Кошелёк клиента совпадает с нашим USDT-кошельком.")

        existing = await self.repo.get_active_payment_watch_by_reply(
            chat_id=command.chat_id,
            reply_message_id=command.reply_message_id,
        )
        if existing:
            raise PaymentWatchError("Для этого сообщения уже запущено ожидание оплаты.")

        mode = "TEST_THEN_MAIN" if command.test_mode else "SINGLE"
        phase = "TEST" if command.test_mode else "MAIN"
        timeout_at = datetime.now(UTC) + timedelta(seconds=self.timeout_seconds)
        if self.fulfillment_queue_service is None:
            raise PaymentWatchError("Учёт вывода USDT недоступен.")
        try:
            started = await self.fulfillment_queue_service.start_client_withdrawal(
                StartFulfillmentExecution(
                    chat_id=command.chat_id,
                    chat_name=command.chat_name,
                    reply_message_id=command.reply_message_id,
                    address=address,
                    our_address=our_address,
                    actor_tg_user_id=command.created_by_user_id,
                    requested_qty=(
                        Decimal(command.manager_note.replace(",", "."))
                        if command.manager_note is not None
                        else None
                    ),
                    mode=mode,
                    phase=phase,
                    timeout_at=timeout_at,
                    command_message_id=command.command_message_id,
                )
            )
        except FulfillmentQueueError as exc:
            raise PaymentWatchError(str(exc)) from exc
        watch_id = started.watch_id
        return PaymentWatchStarted(
            watch_id=watch_id,
            message_text=self.builder.build_started(
                address=address,
                test_mode=command.test_mode,
                manager_note=command.manager_note,
            ),
        )

    async def continue_watch(self, *, watch_id: int) -> str:
        watch = await self.repo.get_payment_watch(watch_id=watch_id)
        if not watch:
            raise PaymentWatchError("Наблюдение не найдено.")
        if str(watch.get("status")) != "TIMED_OUT":
            raise PaymentWatchError("Это наблюдение уже не ожидает подтверждения продолжения.")

        timeout_at = datetime.now(UTC) + timedelta(seconds=self.timeout_seconds)
        updated = await self.repo.continue_payment_watch(watch_id=watch_id, timeout_at=timeout_at)
        if not updated:
            raise PaymentWatchError("Не удалось продлить ожидание.")
        return self.builder.build_continued()

    async def set_notice_message_id(self, *, watch_id: int, message_id: int) -> None:
        await self.repo.set_payment_watch_notice_message_id(
            watch_id=watch_id,
            notice_message_id=message_id,
        )

    async def stop_watch(self, *, watch_id: int) -> str:
        watch = await self.repo.get_payment_watch(watch_id=watch_id)
        if not watch:
            raise PaymentWatchError("Наблюдение не найдено.")
        updated = await self.repo.stop_payment_watch(watch_id=watch_id)
        if not updated:
            raise PaymentWatchError("Ожидание уже остановлено или завершено.")
        return self.builder.build_stopped()

    @measured_operation("payment_watch.poll")
    async def poll_once(self) -> list[PaymentWatchNotification]:
        notifications: list[PaymentWatchNotification] = []
        now = datetime.now(UTC)
        watches = await self.repo.list_watching_payment_watches(limit=100)
        self._metrics.set_queue_size("payment_watch.active", len(watches))
        for watch in watches:
            watch_id = int(watch["id"])
            notice_message_id = int(watch["notice_message_id"]) if watch.get("notice_message_id") else None
            timeout_at = watch.get("timeout_at")
            if timeout_at and timeout_at <= now:
                changed = await self.repo.mark_payment_watch_timed_out(watch_id=watch_id)
                if changed:
                    log.info("Payment watch %s timed out", watch_id)
                    notifications.append(
                        PaymentWatchNotification(
                            chat_id=int(watch["chat_id"]),
                            reply_message_id=int(watch["reply_message_id"]),
                            text=self.builder.build_timeout(),
                            watch_id=watch_id,
                            with_timeout_actions=True,
                            delete_message_id=notice_message_id,
                        )
                    )
                continue

            # Ошибка по одному watch'у (чаще всего рейт-лимит Tronscan) не должна
            # прерывать обработку остальных и терять уже собранные уведомления.
            try:
                notifications.extend(await self._process_watch(watch))
            except TronscanGatewayError as exc:
                log.warning("Payment watch %s check skipped: %s", watch_id, exc)
                continue
            except Exception:
                log.exception("Payment watch %s check failed", watch_id)
                continue
            await self.repo.touch_payment_watch_checked_at(watch_id=watch_id, checked_at=now)
        return notifications

    async def _process_watch(self, watch: dict) -> list[PaymentWatchNotification]:
        watch_id = int(watch["id"])
        address = str(watch["address"])
        our_address = str(watch["our_address"])
        phase = str(watch["phase"]).upper()
        mode = str(watch["mode"]).upper()
        started_at = watch["started_at"]
        start_ms = int(started_at.timestamp() * 1000)
        seen_hashes = await self.repo.get_payment_watch_event_hashes(watch_id=watch_id)
        transfers = await self.tronscan_gateway.list_usdt_transfers(
            address=address,
            start_timestamp_ms=start_ms,
            limit=50,
            skip_confirmation_hashes=seen_hashes,
        )

        notifications: list[PaymentWatchNotification] = []
        for transfer in transfers:
            if transfer.tx_hash in seen_hashes:
                continue
            if transfer.confirmations < 1:
                continue
            if not (
                (transfer.from_address == our_address and transfer.to_address == address)
                or (transfer.from_address == address and transfer.to_address == our_address)
            ):
                continue
            direction = "IN" if transfer.to_address == address else "OUT"

            if mode == "TEST_THEN_MAIN" and phase == "TEST":
                if transfer.amount != self.test_amount:
                    continue
                if self.settlement_service is None:
                    raise PaymentWatchError("Settlement service is not configured")
                await self.settlement_service.settle_test(
                    ConfirmedTransfer(
                        watch_id=watch_id,
                        tx_hash=transfer.tx_hash,
                        direction=direction,
                        amount=transfer.amount,
                        token_symbol=transfer.token_symbol,
                        confirmations=transfer.confirmations,
                        block_ts=transfer.block_ts,
                        from_address=transfer.from_address,
                        to_address=transfer.to_address,
                    )
                )
                log.info(
                    "Payment watch %s test payment detected: tx=%s amount=%s",
                    watch_id, transfer.tx_hash, transfer.amount,
                )
                phase = "MAIN"
                seen_hashes.add(transfer.tx_hash)
                notifications.append(
                    PaymentWatchNotification(
                        chat_id=int(watch["chat_id"]),
                        reply_message_id=int(watch["reply_message_id"]),
                        watch_id=watch_id,
                        text=self.builder.build_test_success(
                            amount=transfer.amount,
                            tx_hash=transfer.tx_hash,
                            from_address=transfer.from_address,
                            to_address=transfer.to_address,
                            block_number=transfer.block_number,
                        ),
                        delete_message_id=int(watch["notice_message_id"]) if watch.get("notice_message_id") else None,
                    )
                )
                continue

            if self.settlement_service is None:
                raise PaymentWatchError("Settlement service is not configured")
            settlement = await self.settlement_service.settle(
                ConfirmedTransfer(
                    watch_id=watch_id,
                    tx_hash=transfer.tx_hash,
                    direction=direction,
                    amount=transfer.amount,
                    token_symbol=transfer.token_symbol,
                    confirmations=transfer.confirmations,
                    block_ts=transfer.block_ts,
                    from_address=transfer.from_address,
                    to_address=transfer.to_address,
                )
            )
            evidence = settlement.evidence
            if evidence is None:
                raise PaymentWatchError("Stored settlement evidence is unavailable")
            log.info(
                "Payment watch %s confirmed (MAIN): tx=%s amount=%s %s confirmations=%s",
                watch_id, transfer.tx_hash, transfer.amount, transfer.token_symbol, transfer.confirmations,
            )
            photo_bytes: bytes | None = None
            try:
                photo_bytes = self.receipt_builder.build_main_success(
                    amount=evidence.amount,
                    recipient_address=evidence.to_address,
                    tx_hash=evidence.tx_hash,
                    block_ts=evidence.block_ts,
                )
            except Exception as exc:  # noqa: BLE001 — генерация чека best-effort: при сбое шлём уведомление без картинки
                log.warning("Payment receipt image disabled: %s", exc)
            notifications.append(
                PaymentWatchNotification(
                    chat_id=int(watch["chat_id"]),
                    reply_message_id=int(watch["reply_message_id"]),
                    text=(
                        self.builder.build_settlement_review(
                            expected=settlement.expected,
                            actual=settlement.actual,
                            tx_hash=evidence.tx_hash,
                        )
                        if settlement.status is SettlementReviewStatus.NEEDS_REVIEW
                        else self.builder.build_main_success(
                            amount=evidence.amount,
                            tx_hash=evidence.tx_hash,
                            direction=evidence.direction,
                        )
                    ),
                    delete_message_id=int(watch["notice_message_id"]) if watch.get("notice_message_id") else None,
                    photo_bytes=photo_bytes,
                    photo_filename=f"payment_receipt_{watch_id}.png" if photo_bytes else None,
                )
            )
            if (
                settlement.created
                and settlement.client_wallet_amount is not None
                and settlement.client_wallet_balance_after is not None
                and settlement.client_wallet_precision is not None
            ):
                amount = settlement.client_wallet_amount
                notifications.append(
                    PaymentWatchNotification(
                        chat_id=int(watch["chat_id"]),
                        reply_message_id=None,
                        text=WalletTextBuilder.currency_change_success(
                            code="USDT",
                            delta=abs(amount),
                            precision=settlement.client_wallet_precision,
                            sign="+" if amount >= 0 else "-",
                            balance=settlement.client_wallet_balance_after,
                        ),
                    )
                )
            break
        return notifications


    async def start_accounting(self, command: StartPaymentWatchCommand, *, key, actor_id, our_address):
        address = extract_tron_address(command.reply_text,command.reply_caption)
        if address is None:
            raise InvalidPosting('В сообщении не найден TRON-кошелёк')
        if not our_address or not is_probable_tron_wallet(our_address) or address==our_address:
            raise InvalidPosting('Нужен корректный USDT-кошелёк кассы, отличный от адреса клиента')
        client_id = await self._accounting_queries.client_by_chat(command.chat_id)
        if client_id is None:
            raise InvalidPosting('Сначала /start в клиентском чате')
        requested = None if command.manager_note is None else exact(decimal(command.manager_note.replace(',','.')),6)
        if requested is not None and requested<=0:
            raise InvalidPosting('Укажите положительную сумму USDT')
        async def action(work):
            await work.clients.lock_client(client_id)
            snapshot = await work.clients.zero_preview(client_id)
            quantity = requested if requested is not None else next(row['balance'] for row in snapshot['wallets'] if row['instrument_code']=='USDT')
            if quantity<=0:
                raise InvalidPosting('Укажите сумму: /отпр суммаUSDT')
            return await work.trades.start_payment_watch(actor_id=actor_id,client_id=client_id,
                chat_id=command.chat_id,reply_message_id=command.reply_message_id,address=address,
                our_address=our_address,quantity=quantity,test_mode=command.test_mode,
                timeout_at=datetime.now(UTC)+timedelta(seconds=self.timeout_seconds))
        return await self._commands.execute(key=key,actor_id=actor_id,kind='payment_watch_start',
            payload={'chat':command.chat_id,'reply':command.reply_message_id,'address':address,
                     'our_address':our_address,'quantity':requested,'test':command.test_mode},
            action=action,permission='finance.post')

    async def control_accounting(self, *, key,actor_id,watch_id,chat_id,action,expected_continue_count):
        async def change(work):
            watch = await work.trades.lock_payment_watch(watch_id)
            if watch['chat_id']!=chat_id or watch['continue_count']!=expected_continue_count:
                raise InvalidPosting('Кнопка не принадлежит этому чату или устарела')
            await work.trades.change_payment_watch(watch_id,action,datetime.now(UTC)+timedelta(seconds=self.timeout_seconds))
            return {'watch_id':watch_id}
        if action not in ('continue','stop'):
            raise InvalidPosting('Неизвестное действие')
        return await self._commands.execute(key=key,actor_id=actor_id,kind='payment_watch_control',
            payload={'watch':watch_id,'action':action,'revision':expected_continue_count,'chat':chat_id},
            action=change,permission='finance.post')

    @staticmethod
    def eligible_transfer(watch, transfer):
        return (transfer.confirmed and transfer.confirmations>=2 and transfer.token_symbol=='USDT'
            and transfer.from_address==watch['our_address'] and transfer.to_address==watch['address']
            and transfer.block_ts>=watch['started_at'])

    async def settle_accounting(self, *, watch_id, transfer):
        watch = await self._accounting_queries.payment_watch(watch_id)
        if not self.eligible_transfer(watch,transfer):
            raise InvalidPosting('Перевод не соответствует наблюдению или двум подтверждениям USDT')
        quantity = exact(decimal(transfer.amount),6)
        if quantity<=0 or not transfer.tx_hash.strip():
            raise InvalidPosting('Некорректный сетевой перевод')
        async with self._accounting_factory() as work:
            await work.journal.lock_posting_period()
            await work.clients.lock_client(watch['client_id'])
            current = await work.trades.lock_payment_watch(watch_id)
            if await work.trades.payment_hash_exists(transfer.tx_hash):
                return None
            if current['status']!='WATCHING':
                raise InvalidPosting('Наблюдение не активно')
            phase = current['phase']
            if phase=='TEST' and quantity!=self.test_amount:
                return None
            if phase=='MAIN' and current['mode']=='TEST_THEN_MAIN':
                confirmed = await self._accounting_queries.payment_watch(watch_id)
                if any(event['event_type']=='TEST' and transfer.block_ts<event['block_ts'] for event in confirmed['events']):
                    return None
            review = phase=='MAIN' and quantity!=current['expected_quantity']
            async def save(operation_id,uow,plan):
                await uow.trades.confirmed_payment(watch_id,operation_id,transfer,phase,review)
            operation = await self._wallets.withdraw(key=f'chain:{transfer.tx_hash}',actor_id=current['created_by_user_id'],
                client_id=current['client_id'],instrument='USDT',quantity=quantity,business_date=transfer.block_ts.astimezone(ZoneInfo('Asia/Bangkok')).date(),
                effective_at=transfer.block_ts,comment=f'USDT TRC-20 {phase}: {transfer.tx_hash}',
                payment_watch_id=watch_id,document=CallbackDocument(save),work=work)
            await work.commit()
            return operation

    async def poll_accounting(self, gateway):
        for watch in await self._accounting_queries.payment_watches():
            try:
                if watch['timeout_at']<=datetime.now(UTC):
                    async with self._accounting_factory() as work:
                        await work.trades.change_payment_watch(watch['id'],'timeout',None)
                        await work.commit()
                    continue
                transfers = await gateway.list_usdt_transfers(address=watch['address'],
                    start_timestamp_ms=int(watch['started_at'].timestamp()*1000))
                for transfer in sorted(transfers,key=lambda item:(item.block_ts,item.tx_hash)):
                    if not self.eligible_transfer(watch,transfer):
                        continue
                    if watch['client_id'] is not None:
                        await self.settle_accounting(watch_id=watch['id'],transfer=transfer)
                    else:
                        await self._trading.post(key=f'chain:{transfer.tx_hash}',actor_id=watch['created_by_user_id'],deal_id=watch['deal_id'],
                            effective_at=transfer.block_ts,business_date=transfer.block_ts.astimezone(ZoneInfo('Asia/Bangkok')).date(),
                            settlement={'watch_id':watch['id'],'tx_hash':transfer.tx_hash,'amount':transfer.amount,
                                'confirmations':transfer.confirmations,'block_ts':transfer.block_ts,'token_symbol':'USDT',
                                'direction':'OUT','address':watch['address'],'our_address':watch['our_address']})
                    current = await self._accounting_queries.payment_watch(watch['id'])
                    if current['status']=='COMPLETED':
                        break
            except (FinanceError,TronscanGatewayError):
                log.warning('Payment watch %s requires retry or manager review',watch['id'])


class PaymentMonitor(ManagedTaskLifecycle):
    """Runtime scheduler facade over the same application service used by the bot."""
    def __init__(self, service: PaymentWatchService, gateway: TronscanGateway):
        super().__init__(task_name='payment_watch')
        self._service,self._gateway = service,gateway

    async def _run(self):
        while not self.is_stopping:
            await self.run_once()
            await asyncio.sleep(10)

    async def run_once(self):
        await self._service.poll_accounting(self._gateway)
