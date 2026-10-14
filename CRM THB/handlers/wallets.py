from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from db_asyncpg.ports.workflows import ManagedClientWalletTransactionRepositoryPort
from db_asyncpg.repositories.client_closure import ClientClosureError, ClientClosureRepo
from domain import DomainStateError, DomainValidationError
from services.accounting.cash_settlement_models import CashSettlementCommand
from services.accounting.cash_settlement_service import (
    CashSettlementError,
    CashSettlementService,
)
from services.crm.client_transfer_service import (
    ClientTransferAdjustmentCommand,
    ClientTransferAdjustmentResult,
    ClientTransferCommand,
    ClientTransferService,
)
from services.crm.client_transfer_workflow import ClientTransferWorkflow
from services.number_formatting import format_amount_core
from services.receipts import ReceiptImageBuilder, ReceiptRow
from services.receipts.image import format_receipt_datetime
from services.wallets import WalletInteractionService
from services.wallets.command_parser import WalletCommandParser
from services.wallets.models import CurrencyChangeCommand
from telegram_adapters import ChatLockRegistry, CityCashMediaStore
from telegram_adapters.auth import (
    manager_or_admin_callback_required,
    manager_or_admin_message_required,
    require_manager_or_admin_message,
)
from telegram_adapters.city_cash_transfer import (
    city_cash_transfer_to_client,
    send_cash_settlement_balance_to_client,
    send_cash_settlement_evidence_to_client,
)
from telegram_adapters.errors import suppress_telegram_edit_errors
from telegram_adapters.message_context import get_chat_name
from telegram_adapters.statements import handle_stmt_callback

_RE_PUBLIC_WALLET_CMD = r"(?iu)^/кош(?:@\w+)?(?:\s|$)"
_RE_CASH_REQUEST_ID = re.compile(r"(?iu)^Б-\d+$")
_TRANSFER_CURRENCIES = frozenset({"RUB", "USDT", "USD", "USDW", "EUR", "EUR500", "THB"})
_TRANSFER_RECEIPT_CAPTION = re.compile(r"^Перевод #(\d+)$")
_UNSET_MARKUP = object()
log = logging.getLogger("wallets")


def _inline_markup(value: object | None) -> InlineKeyboardMarkup | None:
    if value is None or isinstance(value, InlineKeyboardMarkup):
        return value
    raise TypeError(f"Unsupported wallet reply markup: {type(value).__name__}")


def _message_bot(message: Message) -> Bot:
    if message.bot is None:
        raise RuntimeError("Telegram bot is unavailable for this message")
    return message.bot


class WalletsHandler:
    def __init__(
        self,
        repo: ManagedClientWalletTransactionRepositoryPort,
        interaction_service: WalletInteractionService,
        city_cash_media_store: CityCashMediaStore,
        chat_locks: ChatLockRegistry,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
        *,
        ignore_chat_ids: Iterable[int] | None = None,
        silent_chat_ids: Iterable[int] | None = None,
        city_cash_chats: Mapping[str, int] | None = None,
        cash_settlement_service: CashSettlementService | None = None,
        client_transfer_service: ClientTransferService | None = None,
        client_transfer_workflow: ClientTransferWorkflow | None = None,
        client_closure_repo: ClientClosureRepo | None = None,
        default_city: str = "екб",
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.ignore_chat_ids = set(ignore_chat_ids or [])
        self.silent_chat_ids = set(silent_chat_ids or [])
        self.city_cash_chats = dict(city_cash_chats or {})
        self.city_cash_chat_ids = set(self.city_cash_chats.values())
        self.cash_settlement_service = cash_settlement_service
        self.client_transfer_service = client_transfer_service
        self.client_transfer_workflow = client_transfer_workflow
        self.client_closure_repo = client_closure_repo
        self.receipt_builder = ReceiptImageBuilder()
        self.default_city = default_city
        self._background_tasks: set[asyncio.Task[None]] = set()
        self.city_cash_media_store = city_cash_media_store
        self.chat_locks = chat_locks
        self.interaction_service = interaction_service
        self.router = Router()
        self._register()

    async def _process_buffered_city_cash_command(self, message: Message) -> None:
        await asyncio.sleep(0.8)
        async with self.chat_locks.for_chat(message.chat.id):
            await self._execute_currency_change(message)

    @manager_or_admin_message_required
    async def _cmd_wallet(self, message: Message) -> None:
        result = await self.interaction_service.build_wallet_response(
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
        )
        await message.answer(
            result.message_text,
            parse_mode="HTML",
            reply_markup=_inline_markup(result.reply_markup),
        )

    async def _cmd_zero_client(self, message: Message) -> None:
        if self.client_closure_repo is None:
            await message.answer("Обнуление временно недоступно")
            return
        if not message.from_user or not (
            message.from_user.id in self.admin_user_ids
            or await self.repo.is_manager(message.from_user.id)
        ):
            await message.answer("⛔ Команда доступна только менеджерам")
            return
        if (message.chat.type not in {"group", "supergroup"}
                or message.chat.id in self.city_cash_chat_ids | self.silent_chat_ids
                | self.admin_chat_ids | self.ignore_chat_ids):
            await message.answer("/обнулить доступна только в чате клиента")
            return
        if len((message.text or "").strip().split()) != 1:
            await message.answer("Использование: /обнулить")
            return
        try:
            preview = await self.client_closure_repo.preview(message.chat.id)
        except ClientClosureError as exc:
            await message.answer(str(exc))
            return
        lines = [
            f"⚠️ Обнулить клиента «{preview.client_name}» (чат {preview.chat_id})?",
            "Будут созданы корректирующие проводки, клиент и все его счета станут неактивными.",
            "Текущие остатки:",
        ]
        lines.extend(f"{a.currency}: {a.balance}" for a in preview.accounts)
        if not preview.accounts:
            lines.append("Счетов нет")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="Подтвердить обнуление",
                callback_data=f"zero:yes:{message.from_user.id}:{preview.fingerprint}",
            ),
            InlineKeyboardButton(
                text="Отклонить",
                callback_data=f"zero:no:{message.from_user.id}:{preview.fingerprint}",
            ),
        ]])
        await message.answer("\n".join(lines), reply_markup=keyboard)

    async def _cb_zero_client(self, cq: CallbackQuery) -> None:
        if not isinstance(cq.message, Message) or self.client_closure_repo is None:
            await cq.answer("Сообщение недоступно", show_alert=True)
            return
        try:
            _, decision, initiator, fingerprint = (cq.data or "").split(":")
            if decision not in {"yes", "no"} or int(initiator) != cq.from_user.id:
                raise ValueError
        except ValueError:
            await cq.answer("Подтвердить может только менеджер, создавший запрос", show_alert=True)
            return
        if not (cq.from_user.id in self.admin_user_ids
                or await self.repo.is_manager(cq.from_user.id)):
            await cq.answer("Доступ только для менеджеров", show_alert=True)
            return
        if decision == "no":
            await cq.message.edit_text("Обнуление отклонено")
            await cq.answer()
            return
        async with self.chat_locks.for_chat(cq.message.chat.id):
            try:
                preview = await self.client_closure_repo.close(
                    chat_id=cq.message.chat.id,
                    expected_fingerprint=fingerprint,
                    actor_tg_user_id=cq.from_user.id,
                    confirmation_message_id=cq.message.message_id,
                )
            except ClientClosureError as exc:
                await cq.answer(str(exc), show_alert=True)
                return
        await cq.message.edit_text(
            f"✅ Клиент «{preview.client_name}» обнулён. "
            "Все счета и клиент неактивны. Корректирующие проводки сохранены."
        )
        await cq.answer("Обнулено")

    @manager_or_admin_message_required
    async def _cmd_rmcur(self, message: Message) -> None:
        result = await self.interaction_service.build_remove_currency_response(
            raw_text=message.text or "",
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
        )
        await message.answer(result.message_text, reply_markup=_inline_markup(result.reply_markup))

    @manager_or_admin_message_required
    async def _cmd_addcur(self, message: Message) -> None:
        result = await self.interaction_service.build_add_currency_response(
            raw_text=message.text or "",
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
        )
        await message.answer(result.message_text)

    @manager_or_admin_message_required
    async def _cmd_client_transfer(self, message: Message) -> None:
        if self.client_transfer_service is None:
            await message.answer("Переводы временно недоступны")
            return
        if message.chat.id in self.city_cash_chat_ids | self.silent_chat_ids | self.admin_chat_ids:
            await message.answer("Перевод доступен только из клиентского чата")
            return
        receipt_deal_id = self._replied_transfer_id(message)
        if receipt_deal_id is not None:
            parts = (message.text or "").strip().split()
            if len(parts) != 2:
                await message.answer("Для изменения ответьте на чек: /перевод <новая сумма>")
                return
            try:
                new_amount = Decimal(parts[1].replace(",", "."))
                if not new_amount.is_finite() or new_amount <= 0:
                    raise InvalidOperation
            except InvalidOperation:
                await message.answer("Некорректная сумма перевода")
                return
            await self._execute_transfer_adjustment(message, receipt_deal_id, new_amount)
            return
        try:
            amount, currency, recipient_name = self._parse_transfer(message.text or "")
            self._transfer_callback("pick", message.message_id, 1, currency, amount)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        recipients = await self.client_transfer_service.find_recipients(recipient_name)
        if not recipients:
            await message.answer(f"Клиентский чат «{recipient_name}» не найден")
            return
        if len(recipients) > 1:
            buttons = [
                [InlineKeyboardButton(
                    text=f"{item['name']} · {item['chat_id']}",
                    callback_data=self._transfer_callback(
                        "pick", message.message_id, int(item["id"]), currency, amount
                    ),
                )]
                for item in recipients
            ]
            await message.answer(
                "Есть несколько чатов с таким названием. Выберите получателя:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            )
            return
        await self._execute_client_transfer(
            message,
            source_message_id=message.message_id,
            recipient_id=int(recipients[0]["id"]),
            amount=amount,
            currency=currency,
            actor_tg_user_id=message.from_user.id if message.from_user else None,
        )

    @staticmethod
    def _replied_transfer_id(message: Message) -> int | None:
        reply = message.reply_to_message
        if not reply or not reply.photo or not reply.from_user or not message.bot:
            return None
        if reply.from_user.id != message.bot.id:
            return None
        match = _TRANSFER_RECEIPT_CAPTION.fullmatch((reply.caption or "").strip())
        return int(match.group(1)) if match else None

    @manager_or_admin_message_required
    async def _cmd_cancel_transfer(self, message: Message) -> None:
        if self.client_transfer_service is None:
            await message.answer("Переводы временно недоступны")
            return
        deal_id = self._replied_transfer_id(message)
        if deal_id is None:
            await message.answer("Ответьте командой /отмена на чек перевода в чате отправителя")
            return
        await self._execute_transfer_adjustment(message, deal_id, None)

    async def _execute_transfer_adjustment(
        self,
        message: Message,
        deal_id: int,
        new_amount: Decimal | None,
        *,
        source_message_id: int | None = None,
        allow_negative: bool = False,
        actor_tg_user_id: int | None = None,
    ) -> None:
        assert self.client_transfer_service is not None
        source_message_id = source_message_id or message.message_id
        try:
            result = await self.client_transfer_service.adjust(ClientTransferAdjustmentCommand(
                deal_id=deal_id,
                from_chat_id=message.chat.id,
                source_ref=f"{message.chat.id}:{source_message_id}",
                new_amount=new_amount,
                actor_tg_user_id=actor_tg_user_id or (message.from_user.id if message.from_user else None),
                allow_negative=allow_negative,
            ))
        except DomainStateError as exc:
            if "Недостаточно средств" not in str(exc) or allow_negative:
                await message.answer(str(exc))
                return
            raw_amount = "cancel" if new_amount is None else str(new_amount)
            data = f"cta:{deal_id}:{source_message_id}:{raw_amount}"
            if len(data.encode("utf-8")) > 64:
                await message.answer("Сумма слишком длинная для подтверждения в Telegram")
                return
            await message.answer(
                f"⚠️ {exc}\nПодтвердить операцию?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Подтвердить", callback_data=data),
                    InlineKeyboardButton(text="Отклонить", callback_data="cta:reject"),
                ]]),
            )
            return
        except DomainValidationError as exc:
            await message.answer(str(exc))
            return
        await self._report_transfer_adjustment(message, result)

    async def _report_transfer_adjustment(
        self, message: Message, result: ClientTransferAdjustmentResult
    ) -> None:
        if result.repeated:
            await message.answer("Эта корректировка уже применена или сумма не изменилась")
            return
        status = "отменён" if result.canceled else "изменён"
        amount = format_amount_core(result.new_amount, result.precision)
        if not result.canceled and message.bot is not None:
            try:
                receipt = self.receipt_builder.build(
                    amount=result.new_amount,
                    currency=result.currency,
                    precision=result.precision,
                    rows=(
                        ReceiptRow("Статус", "Изменено", accent=True),
                        ReceiptRow("Номер операции", f"#{result.deal_id}"),
                        ReceiptRow("Отправитель", result.from_client_name),
                        ReceiptRow("Получатель", result.to_client_name),
                        ReceiptRow("Дата и время", format_receipt_datetime(message.date)),
                    ),
                )
                for chat_id in (message.chat.id, result.to_chat_id):
                    await message.bot.send_photo(
                        chat_id,
                        BufferedInputFile(receipt, filename=f"client_transfer_{result.deal_id}_updated.png"),
                        caption=f"Перевод #{result.deal_id}",
                    )
            except Exception:
                log.exception("Failed to send corrected transfer receipt deal_id=%s", result.deal_id)
        await message.answer(
            f"Перевод #{result.deal_id} {status}.\n"
            f"Сумма: {amount} {result.currency}\n"
            f"Баланс: {format_amount_core(result.from_balance, result.precision)} {result.currency}"
        )
        if message.bot is not None:
            try:
                await message.bot.send_message(
                    result.to_chat_id,
                    f"Перевод #{result.deal_id} {status}.\n"
                    f"Сумма: {amount} {result.currency}\n"
                    f"Баланс: {format_amount_core(result.to_balance, result.precision)} {result.currency}",
                )
            except Exception:
                log.exception("Failed to notify transfer adjustment deal_id=%s", result.deal_id)

    @staticmethod
    def _parse_transfer(raw_text: str, *, default_currency: str = "RUB", aliases: Mapping[str, str] | None = None, allow_expressions: bool = False) -> tuple[Decimal, str, str]:
        if allow_expressions:
            import shlex
            words = shlex.split(raw_text)
            parts = [*words[:2], ' '.join(words[2:])] if len(words)>=3 else words
        else:
            parts = raw_text.strip().split(maxsplit=2)
        if len(parts) < 3:
            raise ValueError(
                "Использование: /перевод 1000 Имя чата или /перевод 100 USDT Имя чата"
            )
        try:
            if allow_expressions:
                from services.expression_calculator import CalcError, evaluate
                if len(parts[1])>200:
                    raise ValueError("Слишком длинная сумма перевода")
                try:
                    amount = evaluate(parts[1])
                except CalcError:
                    raise ValueError("Некорректная сумма перевода") from None
            else:
                amount = Decimal(parts[1].replace(",", "."))
        except InvalidOperation:
            raise ValueError("Некорректная сумма перевода") from None
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Сумма перевода должна быть больше нуля")
        tail = parts[2].strip()
        first, _, remainder = tail.partition(" ")
        normalized = aliases.get(first.lower()) if aliases is not None else WalletCommandParser.normalize_code_alias(first)
        if normalized is not None and (aliases is not None or normalized in _TRANSFER_CURRENCIES) and remainder.strip():
            return amount, normalized, remainder.strip()
        return amount, default_currency, tail

    @staticmethod
    def _transfer_callback(
        phase: str, message_id: int, recipient_id: int, currency: str, amount: Decimal
    ) -> str:
        data = f"ct:{phase}:{message_id}:{recipient_id}:{currency}:{amount}"
        if len(data.encode("utf-8")) > 64:
            raise ValueError("Сумма слишком длинная для подтверждения в Telegram")
        return data

    async def _execute_client_transfer(
        self,
        message: Message,
        *,
        source_message_id: int,
        recipient_id: int,
        amount: Decimal,
        currency: str,
        allow_negative: bool = False,
        actor_tg_user_id: int | None = None,
    ) -> None:
        assert self.client_transfer_workflow is not None
        try:
            await self.client_transfer_workflow.transfer(
                ClientTransferCommand(
                    amount=amount,
                    currency=currency,
                    source="tg_bot",
                    source_ref=f"{message.chat.id}:{source_message_id}",
                    city=self.default_city,
                    from_chat_id=message.chat.id,
                    to_client_id=recipient_id,
                    allow_negative=allow_negative,
                    actor_tg_user_id=actor_tg_user_id,
                ),
                reply_to_message_id=message.message_id,
            )
        except DomainStateError as exc:
            if "Недостаточно средств" not in str(exc) or allow_negative:
                await message.answer(str(exc))
                return
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Подтвердить перевод",
                        callback_data=self._transfer_callback(
                            "confirm", source_message_id, recipient_id, currency, amount
                        ),
                    ),
                    InlineKeyboardButton(
                        text="Отклонить",
                        callback_data=self._transfer_callback(
                            "reject", source_message_id, recipient_id, currency, amount
                        ),
                    ),
                ]
            ])
            await message.answer(
                f"⚠️ {exc}\nПровести перевод?",
                reply_markup=keyboard,
            )
            return
        except DomainValidationError as exc:
            await message.answer(str(exc))
            return

    @manager_or_admin_callback_required
    async def _cb_client_transfer(self, cq: CallbackQuery) -> None:
        if not isinstance(cq.message, Message) or self.client_transfer_service is None:
            await cq.answer("Сообщение недоступно", show_alert=True)
            return
        try:
            _, phase, source_message_id, recipient_id, currency, raw_amount = (
                cq.data or ""
            ).split(":", maxsplit=5)
            amount = Decimal(raw_amount)
            parsed_source_message_id = int(source_message_id)
            parsed_recipient_id = int(recipient_id)
            if phase not in {"pick", "confirm", "reject"} or currency not in _TRANSFER_CURRENCIES:
                raise ValueError
        except (ValueError, InvalidOperation):
            await cq.answer("Некорректное подтверждение", show_alert=True)
            return
        if phase == "reject":
            try:
                rejected = await self.client_transfer_service.reject(
                    ClientTransferCommand(
                        amount=amount,
                        currency=currency,
                        source="tg_bot",
                        source_ref=f"{cq.message.chat.id}:{parsed_source_message_id}",
                        city=self.default_city,
                        from_chat_id=cq.message.chat.id,
                        to_client_id=parsed_recipient_id,
                        actor_tg_user_id=cq.from_user.id,
                    )
                )
            except (DomainStateError, DomainValidationError) as exc:
                await cq.answer(str(exc), show_alert=True)
                return
            await cq.message.edit_text(
                "Перевод отклонён" if rejected else "Перевод уже проведён"
            )
            await cq.answer("Отклонено" if rejected else "Уже проведено")
            return
        await self._execute_client_transfer(
            cq.message,
            source_message_id=parsed_source_message_id,
            recipient_id=parsed_recipient_id,
            amount=amount,
            currency=currency,
            allow_negative=phase == "confirm",
            actor_tg_user_id=cq.from_user.id,
        )
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.answer()

    @manager_or_admin_callback_required
    async def _cb_transfer_adjustment(self, cq: CallbackQuery) -> None:
        if not isinstance(cq.message, Message) or self.client_transfer_service is None:
            await cq.answer("Сообщение недоступно", show_alert=True)
            return
        if cq.data == "cta:reject":
            await cq.message.edit_reply_markup(reply_markup=None)
            await cq.answer("Отклонено")
            return
        try:
            _, raw_deal_id, raw_source_id, raw_amount = (cq.data or "").split(":", 3)
            deal_id = int(raw_deal_id)
            source_id = int(raw_source_id)
            new_amount = None if raw_amount == "cancel" else Decimal(raw_amount)
        except (ValueError, InvalidOperation):
            await cq.answer("Некорректное подтверждение", show_alert=True)
            return
        await self._execute_transfer_adjustment(
            cq.message, deal_id, new_amount,
            source_message_id=source_id, allow_negative=True,
            actor_tg_user_id=cq.from_user.id,
        )
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.answer()

    async def on_currency_change(self, message: Message) -> None:
        """Handle wallet commands from router or silent-accounting middleware."""
        await self._on_currency_change(message)

    async def _on_currency_change(self, message: Message) -> None:
        if message.from_user and message.bot and message.from_user.id == message.bot.id:
            return

        if message.chat and message.chat.id in self.ignore_chat_ids:
            return

        if (
            message.media_group_id
            and message.photo
            and message.chat.id in self.city_cash_chat_ids
        ):
            self.city_cash_media_store.add_message(
                chat_id=message.chat.id,
                media_group_id=message.media_group_id,
                message=message,
            )
            task = asyncio.create_task(
                self._process_buffered_city_cash_command(message),
                name=f"city_cash_album:{message.chat.id}:{message.media_group_id}:{message.message_id}",
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return

        text = message.text or message.caption or ""
        reply = getattr(message, "reply_to_message", None)
        if (
            reply
            and message.bot
            and reply.from_user
            and reply.from_user.id == message.bot.id
            and re.match(_RE_PUBLIC_WALLET_CMD, text.strip())
        ):
            return

        if (
            message.chat.id not in self.silent_chat_ids
            and not await require_manager_or_admin_message(
                self.repo,
                message,
                admin_chat_ids=self.admin_chat_ids,
                admin_user_ids=self.admin_user_ids,
            )
        ):
            return

        async with self.chat_locks.for_chat(message.chat.id):
            await self._execute_currency_change(message)

    async def _execute_currency_change(self, message: Message) -> None:
        raw_text = message.text or message.caption or ""
        if message.chat.id in self.silent_chat_ids:
            try:
                partner_send = (
                    self.interaction_service.wallet_service.parser.parse_partner_usdt_send(
                        raw_text
                    )
                )
            except ValueError:
                return
            if partner_send is not None:
                wallet_service = self.interaction_service.wallet_service
                if partner_send.amount is None:
                    await wallet_service.withdraw_all(
                        chat_id=message.chat.id,
                        chat_name=get_chat_name(message),
                        code="USDT",
                        comment=partner_send.raw_text,
                        source="partner_chat_command",
                        idempotency_key=f"{message.chat.id}:{message.message_id}",
                    )
                else:
                    await wallet_service.apply_external_currency_change(
                        chat_id=message.chat.id,
                        chat_name=get_chat_name(message),
                        code="USDT",
                        amount=-partner_send.amount,
                        expr=partner_send.raw_text,
                        source="partner_chat_command",
                        idempotency_key=f"{message.chat.id}:{message.message_id}",
                    )
                return
        try:
            parsed = self.interaction_service.wallet_service.parse_currency_change(
                raw_text,
                chat_id=message.chat.id,
            )
        except ValueError as exc:
            await self._answer(message, str(exc))
            return
        if parsed is None:
            return
        request_id = parsed.client_name_for_transfer.strip()
        if (
            parsed.is_city_cash
            and self.cash_settlement_service is not None
            and request_id
            and not _RE_CASH_REQUEST_ID.fullmatch(request_id)
        ):
            await self._answer(
                message,
                "Для движения клиентской наличности укажите номер заявки, "
                "например: /usd -125 Б-123456. Связь по имени отключена."
            )
            return
        if (
            parsed.is_city_cash
            and self.cash_settlement_service is not None
            and _RE_CASH_REQUEST_ID.fullmatch(request_id)
        ):
            evidence_messages = [message]
            if message.media_group_id:
                grouped = self.city_cash_media_store.pop_group(
                    chat_id=message.chat.id,
                    media_group_id=message.media_group_id,
                )
                if grouped:
                    evidence_messages = grouped
            try:
                settled = await self.cash_settlement_service.settle(
                    CashSettlementCommand(
                        request_id=request_id,
                        city_chat_id=message.chat.id,
                        command_message_id=message.message_id,
                        currency=parsed.code,
                        signed_qty=parsed.amount,
                        actor_tg_user_id=(
                            message.from_user.id if message.from_user is not None else None
                        ),
                        evidence={
                            "telegramChatId": message.chat.id,
                            "telegramMessageId": message.message_id,
                            "mediaGroupId": message.media_group_id,
                            "photoFileIds": [
                                photo.file_id
                                for evidence_message in evidence_messages
                                for photo in (evidence_message.photo or [])[-1:]
                            ],
                            "comment": parsed.extra_comment,
                        },
                    )
                )
            except (CashSettlementError, DomainStateError, DomainValidationError) as exc:
                await self._answer(message, str(exc))
                return
            suffix = " (повтор)" if settled.repeated else ""
            delivery_warning = ""
            client_chat_id = settled.client_chat_id
            if any(evidence_message.photo for evidence_message in evidence_messages):
                try:
                    client_chat_id = await send_cash_settlement_evidence_to_client(
                        repo=self.repo,
                        bot=_message_bot(message),
                        evidence_messages=evidence_messages,
                        target_chat_id=settled.client_chat_id,
                        target_client_id=settled.client_id,
                    )
                except Exception:
                    log.exception(
                        "FAILED sending cash settlement evidence request_id=%s "
                        "client_id=%s chat_id=%s",
                        settled.request_id,
                        settled.client_id,
                        settled.client_chat_id,
                    )
                    delivery_warning = (
                        "\n⚠️ Не удалось отправить фото в чат клиента."
                    )
            signed_qty = (
                settled.actual_qty
                if settled.request_kind == "dep"
                else -settled.actual_qty
            )
            if (
                settled.client_balance is not None
                and settled.client_precision is not None
            ):
                try:
                    client_chat_id = await send_cash_settlement_balance_to_client(
                        repo=self.repo,
                        bot=_message_bot(message),
                        target_chat_id=client_chat_id,
                        target_client_id=settled.client_id,
                        currency_code=settled.currency,
                        signed_amount=signed_qty,
                        balance=settled.client_balance,
                        precision=settled.client_precision,
                    )
                except Exception:
                    log.exception(
                        "FAILED sending cash settlement client balance "
                        "request_id=%s client_id=%s chat_id=%s",
                        settled.request_id,
                        settled.client_id,
                        client_chat_id,
                    )
                    delivery_warning += (
                        "\n⚠️ Не удалось отправить баланс в чат клиента."
                    )
            await self._answer(
                message,
                f"Заявка {settled.request_id} проведена{suffix}!\n"
                f"Клиент: {settled.client_name}{delivery_warning}",
            )
            await self._answer(
                message,
                "Запомнил. "
                f"{format_amount_core(signed_qty, settled.cash_precision)} "
                f"{settled.currency.lower()}\n"
                "Баланс: "
                f"{format_amount_core(settled.cash_balance, settled.cash_precision)} "
                f"{settled.currency.lower()}",
            )
            return
        result = await self.interaction_service.build_currency_change_response(
            CurrencyChangeCommand(
                chat_id=message.chat.id,
                chat_name=get_chat_name(message),
                message_id=message.message_id,
                parsed=parsed,
            )
        )
        text = result.message_text
        if result.ok and parsed.is_city_cash and parsed.client_name_for_transfer:
            transfer = await city_cash_transfer_to_client(
                repo=self.repo,
                bot=_message_bot(message),
                src_message=message,
                media_store=self.city_cash_media_store,
                currency_code=parsed.code,
                amount_signed=parsed.amount,
                amount_expr=parsed.expr,
                client_name_exact=parsed.client_name_for_transfer,
                extra_comment=parsed.extra_comment,
            )
            if transfer.ok:
                text += "\n✅ Транзакция проведена в чате у клиента!"
            else:
                text += (
                    "\n⚠️ "
                    + (transfer.error or "Не удалось продублировать операцию в чат клиента.")
                )
        await self._answer(message, text, reply_markup=result.reply_markup)

    async def _answer(
        self, message: Message, text: str, *, reply_markup: object = _UNSET_MARKUP
    ) -> None:
        if message.chat.id in self.silent_chat_ids:
            return
        if reply_markup is _UNSET_MARKUP:
            await message.answer(text)
        else:
            await message.answer(text, reply_markup=_inline_markup(reply_markup))

    async def _buffer_city_cash_media_group(self, message: Message) -> None:
        if not message.media_group_id or not message.photo:
            return
        if message.chat.id not in self.city_cash_chat_ids:
            return
        self.city_cash_media_store.add_message(
            chat_id=message.chat.id,
            media_group_id=message.media_group_id,
            message=message,
        )

    @manager_or_admin_callback_required
    async def _cb_rmcur(self, cq: CallbackQuery) -> None:
        try:
            code_raw, answer = self.interaction_service.parse_remove_currency_callback(cq.data)
        except ValueError:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        if not isinstance(cq.message, Message):
            await cq.answer("Нет чата", show_alert=True)
            return

        edit_text, answer_text, show_alert = await self.interaction_service.build_remove_currency_callback_response(
            chat_id=cq.message.chat.id,
            chat_name=get_chat_name(cq.message),
            code_raw=code_raw,
            answer=answer,
        )
        await cq.message.edit_text(edit_text)
        await cq.answer(answer_text, show_alert=show_alert)

    @manager_or_admin_callback_required
    async def _cb_undo(self, cq: CallbackQuery) -> None:
        try:
            parsed_undo = self.interaction_service.parse_undo_callback(cq.data)
            if not parsed_undo:
                return
            code_raw, sign, amt_str = parsed_undo
        except ValueError:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        if not isinstance(cq.message, Message):
            await cq.answer("Нет сообщения", show_alert=True)
            return

        async with self.chat_locks.for_chat(cq.message.chat.id):
            result = await self.interaction_service.build_undo_response(
                chat_id=cq.message.chat.id,
                chat_name=get_chat_name(cq.message),
                message_id=cq.message.message_id,
                code_raw=code_raw,
                sign=sign,
                amt_str=amt_str,
            )

            if result.ok:
                old_text = cq.message.text or ""
                with suppress_telegram_edit_errors(context="wallet undo"):
                    await cq.message.edit_text(old_text + "\n↩️ Отменено.")

            with suppress_telegram_edit_errors(context="wallet undo"):
                await cq.message.edit_reply_markup(reply_markup=None)

            await cq.message.answer(result.message_text, parse_mode="HTML")
            await cq.answer("Откат выполнен" if result.ok else result.message_text[:100], show_alert=not result.ok)

    async def _cb_statement(self, cq: CallbackQuery) -> None:
        await handle_stmt_callback(cq, self.repo)

    def _register(self) -> None:
        self.router.message.register(self._cmd_zero_client, Command("обнулить"))
        self.router.message.register(self._cmd_wallet, Command("кошелек"))
        self.router.message.register(self._cmd_addcur, Command("добавь"))
        self.router.message.register(self._cmd_rmcur, Command("удали"))
        self.router.message.register(self._cmd_client_transfer, Command("перевод"))
        self.router.message.register(self._cmd_cancel_transfer, Command("отмена"))

        self.router.message.register(
            self._on_currency_change,
            F.text.regexp(r"^/[A-Za-zА-Яа-я0-9_]+\s+"),
        )
        self.router.message.register(
            self._on_currency_change,
            F.caption.regexp(r"^/[A-Za-zА-Яа-я0-9_]+\s+"),
        )
        self.router.message.register(
            self._buffer_city_cash_media_group,
            F.media_group_id,
        )

        self.router.callback_query.register(self._cb_rmcur, F.data.startswith("rmcur:"))
        self.router.callback_query.register(self._cb_zero_client, F.data.startswith("zero:"))
        self.router.callback_query.register(self._cb_undo, F.data.startswith("undo:"))
        self.router.callback_query.register(self._cb_statement, F.data.in_({"stmt:month", "stmt:all"}))
        self.router.callback_query.register(self._cb_client_transfer, F.data.startswith("ct:"))
        self.router.callback_query.register(self._cb_transfer_adjustment, F.data.startswith("cta:"))


    @staticmethod
    def register_accounting(router, commands):
        WalletsHandler.register_accounting_wallets(router,commands)
        async def callback(cq):
            from domain.finance.models import FinanceError, InvalidPosting
            from services.finance.outbox import DeliveryError
            if not isinstance(cq.message,Message):
                await cq.answer('Сообщение недоступно',show_alert=True)
                return
            try:
                phase,source_id,recipient_id = (cq.data or '')[3:].split(':')
                if phase not in ('pick','confirm','reject'):
                    raise InvalidPosting('Некорректное подтверждение')
                actor = await commands.authorize(cq.from_user.id)
                key = ClientTransferService.request_key(cq.message.chat.id,int(source_id))
                request = await commands._app.client_queries.transfer_request(key)
                if phase=='pick':
                    await WalletsHandler._accounting_confirmation(cq.message,request,int(source_id),int(recipient_id))
                else:
                    from datetime import UTC, datetime
                    from zoneinfo import ZoneInfo
                    now = datetime.now(UTC)
                    result = await commands._app.client_transfers.resolve_accounting(key=key,actor_id=actor,
                        recipient_id=int(recipient_id),confirmed=phase=='confirm',effective_at=now,
                        business_date=now.astimezone(ZoneInfo('Asia/Bangkok')).date())
                    if result['operation_id']:
                        await commands.deliver_client_transfer(cq.bot,result['operation_id'])
                    await cq.message.edit_text('Перевод отклонён' if result['cancelled'] else 'Перевод проведён')
                await cq.answer()
            except (FinanceError,ValueError) as error:
                await cq.answer(str(error)[:190],show_alert=True)
            except DeliveryError:
                await cq.answer('Перевод сохранён. Чеки будут отправлены повторно.',show_alert=True)
        router.callback_query.register(callback,F.data.startswith('ct:'))

    @staticmethod
    async def _accounting_confirmation(message, request, source_id, recipient_id):
        from domain.finance.models import InvalidPosting
        from services.wallets.compact_formatter import label_for
        recipient = next((r for r in request['candidates'] if r['id']==recipient_id),None)
        if recipient is None:
            raise InvalidPosting('Получатель не принадлежит этой заявке')
        precision = 6 if request['instrument']=='USDT' else 2
        amount = format_amount_core(Decimal(request['quantity']),precision)
        await message.answer(f"Перевести {amount} {label_for(request['instrument'])}?\nПолучатель: {recipient['name']} · {recipient['chat_id']}",
            parse_mode=None,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text='Подтвердить перевод',callback_data=f'ct:confirm:{source_id}:{recipient_id}'),
                InlineKeyboardButton(text='Отклонить',callback_data=f'ct:reject:{source_id}:{recipient_id}')]]))

    @staticmethod
    async def accounting_transfer(commands, message, *, cancel=False):
        from zoneinfo import ZoneInfo

        from domain.finance.models import FinanceError, InvalidPosting
        from services.finance.outbox import DeliveryError
        if getattr(message,'forward_origin',None) or getattr(message,'forward_from',None):
            return True
        reply = message.reply_to_message
        operation_id = None
        if reply and reply.from_user and reply.from_user.id==message.bot.id:
            operation_id = await commands._app.client_queries.transfer_by_message(message.chat.id,reply.message_id)
        if cancel and operation_id is None:
            return False
        try:
            actor = await commands.authorize(message.from_user.id)
            if reply and operation_id is None:
                raise InvalidPosting('Ответьте на оригинальный чек перевода этого бота')
            common = {'actor_id':actor,'effective_at':message.date,
                      'business_date':message.date.astimezone(ZoneInfo('Asia/Bangkok')).date()}
            if operation_id is not None:
                parts = (message.text or '').split()
                if len(parts)!=(1 if cancel else 2):
                    raise InvalidPosting('Ответьте на чек: /перевод новая_сумма или /отмена')
                result = await commands._app.client_transfers.adjust_accounting(
                    key=f'tg:{message.chat.id}:{message.message_id}:transfer-adjust',operation_id=operation_id,
                    new_amount=None if cancel else parts[1].replace(',','.'),source_chat_id=message.chat.id,**common)
                await commands.deliver_client_transfer(message.bot,result['root_id'])
                await message.answer('Перевод отменён' if result['cancelled'] else 'Перевод изменён')
                return True
            amount,currency,name = WalletsHandler._parse_transfer(message.text or '',
                default_currency='THB_CASH',aliases=commands.transfer_aliases,allow_expressions=True)
            source = await commands._app.trading_queries.client_by_chat(message.chat.id)
            if source is None or message.chat.id==commands.operations_chat_id:
                raise InvalidPosting('Перевод доступен только из клиентского чата')
            request = await commands._app.client_transfers.prepare_accounting(
                key=ClientTransferService.request_key(message.chat.id,message.message_id),actor_id=actor,
                source_client_id=source,source_chat_id=message.chat.id,quantity=amount,instrument=currency,
                recipient_name=name,effective_at=message.date)
            candidates = request['candidates']
            if len(candidates)==1:
                await WalletsHandler._accounting_confirmation(message,request,message.message_id,candidates[0]['id'])
            else:
                await message.answer('Есть несколько чатов с таким названием. Выберите получателя:',
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text=f"{r['name']} · {r['chat_id']}",
                            callback_data=f"ct:pick:{message.message_id}:{r['id']}")]
                        for r in candidates]))
        except (FinanceError,ValueError) as error:
            await message.answer(str(error))
        except DeliveryError:
            await message.answer('Перевод сохранён. Чеки будут отправлены повторно.')
        return True


    @staticmethod
    def register_accounting_wallets(router,commands):
        from domain.finance.models import FinanceError, InvalidPosting
        from services.finance.outbox import DeliveryError

        async def statement(cq):
            if not isinstance(cq.message,Message):
                await cq.answer('Сообщение недоступно',show_alert=True)
                return
            try:
                client = await commands._app.trading_queries.client_by_chat(cq.message.chat.id)
                if client is None:
                    raise InvalidPosting('Сначала /start в этом клиентском чате')
                if cq.data in ('stmt:month','stmt:all'):
                    await handle_stmt_callback(cq,accounting_service=commands._app.wallet_interactions,
                        client_id=client,timezone='Asia/Bangkok')
                else:
                    _,_,code,before = (cq.data or '').split(':')
                    if code not in set(commands.transfer_aliases.values()):
                        raise InvalidPosting('Неизвестный счёт')
                    await WalletsHandler._accounting_statement_page(commands,cq.message,client,code,int(before) or None)
                    await cq.answer()
            except (FinanceError,ValueError) as error:
                await cq.answer(str(error)[:190],show_alert=True)

        async def undo(cq):
            if not isinstance(cq.message,Message):
                await cq.answer('Сообщение недоступно',show_alert=True)
                return
            try:
                from datetime import UTC, datetime
                from zoneinfo import ZoneInfo
                actor = await commands.authorize(cq.from_user.id)
                now = datetime.now(UTC)
                result = await commands._app.wallet_interactions.accounting_undo(chat_id=cq.message.chat.id,
                    message_id=cq.message.message_id,actor_id=actor,effective_at=now,
                    business_date=now.astimezone(ZoneInfo('Asia/Bangkok')).date())
                await commands.deliver_wallet_adjustment(cq.bot,result['original_id'])
                await cq.answer('Операция отменена')
            except (FinanceError,ValueError) as error:
                await cq.answer(str(error)[:190],show_alert=True)
            except DeliveryError:
                await cq.answer('Операция отменена. Сообщение будет обновлено повторно.',show_alert=True)

        async def zero(cq):
            if not isinstance(cq.message,Message):
                await cq.answer('Сообщение недоступно',show_alert=True)
                return
            try:
                from datetime import UTC, datetime
                from zoneinfo import ZoneInfo
                phase,mid = (cq.data or '')[5:].split(':')
                if phase not in ('yes','no'):
                    raise InvalidPosting('Некорректное подтверждение')
                actor = await commands.authorize(cq.from_user.id)
                now = datetime.now(UTC)
                result = await commands._app.wallet_interactions.resolve_zero(
                    key=f'tg:{cq.message.chat.id}:{int(mid)}:zero',actor_id=actor,confirmed=phase=='yes',
                    effective_at=now,business_date=now.astimezone(ZoneInfo('Asia/Bangkok')).date())
                await cq.message.edit_text('Обнуление отклонено' if result['cancelled'] else
                    'Клиент обнулён и закрыт. Корректирующие проводки и история сохранены.')
                await cq.answer()
            except (FinanceError,ValueError) as error:
                await cq.answer(str(error)[:190],show_alert=True)
        router.callback_query.register(statement,F.data.startswith('stmt:'))
        router.callback_query.register(undo,F.data.startswith('undo:'))
        router.callback_query.register(zero,F.data.startswith('zero:'))

    @staticmethod
    async def _accounting_statement_page(commands,message,client,code,before=None):
        from telegram_adapters.statements import statements_kb
        page = await commands._app.wallet_interactions.statement_page(client_id=client,instrument=code,before_id=before)
        buttons = []
        if page['next_before_id']:
            buttons.append([InlineKeyboardButton(text='Далее →',callback_data=f"stmt:page:{code}:{page['next_before_id']}")])
        if before:
            buttons.append([InlineKeyboardButton(text='К последним операциям',callback_data=f'stmt:page:{code}:0')])
        buttons.extend(statements_kb().inline_keyboard)
        await message.answer(page['text'],parse_mode=None,reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    @staticmethod
    async def accounting_wallet_command(commands,message,command):
        from domain.finance.models import InvalidPosting
        from services.finance.outbox import DeliveryError
        from telegram_adapters.statements import statements_kb
        words = (message.text or '').split()
        currency_change = command[1:] in commands.transfer_aliases and len(words)>1
        if currency_change and len(words)==3 and _RE_CASH_REQUEST_ID.fullmatch(words[-1]):
            return False
        if command not in ('/кошелек','/кошелёк','/дай','/выписка','/обнулить') and not currency_change:
            return False
        if (getattr(message,'forward_origin',None) or getattr(message,'forward_from',None)) and (currency_change or command=='/обнулить'):
            return True
        if command=='/обнулить':
            if len(words)!=1:
                raise InvalidPosting('Использование: /обнулить')
            actor = await commands.authorize(message.from_user.id)
            client = await commands._app.trading_queries.client_by_chat(message.chat.id)
            if client is None or message.chat.id==commands.operations_chat_id:
                raise InvalidPosting('/обнулить доступна только в чате клиента')
            preview = await commands._app.wallet_interactions.prepare_zero(
                key=f'tg:{message.chat.id}:{message.message_id}:zero',actor_id=actor,client_id=client)
            rows = [f"⚠️ Обнулить и закрыть клиента «{preview['client_name']}»?",
                    'Будут созданы корректирующие проводки. История сохранится.','Текущие остатки:']
            rows.extend(f"{row['instrument_code']}: {row['balance']}" for row in preview['wallets'])
            await message.answer('\n'.join(rows),parse_mode=None,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text='Подтвердить обнуление',callback_data=f'zero:yes:{message.message_id}'),
                InlineKeyboardButton(text='Отклонить',callback_data=f'zero:no:{message.message_id}')]]))
            return True
        if command=='/выписка':
            client = await commands._app.trading_queries.client_by_chat(message.chat.id)
            if client is None or len(words)>2:
                raise InvalidPosting('/выписка [валюта] — в зарегистрированном клиентском чате')
            code = commands.resolve_instrument(words[1]) if len(words)==2 else 'THB_CASH'
            await WalletsHandler._accounting_statement_page(commands,message,client,code)
            return True
        text = await commands.execute(telegram_user_id=message.from_user.id,chat_id=message.chat.id,
            message_id=message.message_id,text=message.text,effective_at=message.date,
            chat_name=get_chat_name(message),actor_name=message.from_user.full_name)
        if currency_change:
            operation = await commands._app.pool.fetchval('SELECT id FROM financial_operations WHERE idempotency_key=$1',
                f'tg:{message.chat.id}:{message.message_id}')
            try:
                await commands.deliver_wallet_adjustment(message.bot,operation)
            except DeliveryError:
                await message.answer('Корректировка сохранена. Сообщение будет доставлено повторно.')
        else:
            await message.answer(text,parse_mode='HTML' if text.startswith('<code>') else None,
                reply_markup=statements_kb())
        return True
