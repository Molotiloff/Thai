from __future__ import annotations

import logging
from decimal import Decimal
from html import escape

from services.messaging import MessengerPort
from services.number_formatting import format_amount_core
from services.receipts import ReceiptImageBuilder, ReceiptRow
from services.receipts.image import format_receipt_datetime
from services.wallets.compact_formatter import label_for

from .client_transfer_models import ClientTransferCommand, ClientTransferResult
from .client_transfer_service import ClientTransferService

log = logging.getLogger(__name__)


class ClientTransferWorkflow:
    """The same posting, receipt and notification flow for Telegram and CRM."""

    def __init__(
        self,
        service: ClientTransferService,
        messenger: MessengerPort | None = None,
        *,
        receipt_builder: ReceiptImageBuilder | None = None,
    ) -> None:
        self._service = service
        self._messenger = messenger
        self._receipt_builder = receipt_builder or ReceiptImageBuilder()

    async def transfer(
        self,
        command: ClientTransferCommand,
        *,
        reply_to_message_id: int | None = None,
    ) -> ClientTransferResult:
        result = await self._service.transfer(command)
        await self._notify(result, reply_to_message_id=reply_to_message_id)
        return result

    async def _notify(
        self,
        result: ClientTransferResult,
        *,
        reply_to_message_id: int | None,
    ) -> None:
        if not result.repeated:
            try:
                receipt = self._receipt_builder.build(
                    amount=result.amount,
                    currency=result.currency,
                    precision=result.precision,
                    rows=(
                        ReceiptRow("Статус", "Проведено", accent=True),
                        ReceiptRow("Номер операции", f"#{result.deal_id}"),
                        ReceiptRow("Отправитель", result.from_client_name),
                        ReceiptRow("Получатель", result.to_client_name),
                        ReceiptRow("Дата и время", format_receipt_datetime(result.created_at)),
                    ),
                )
            except Exception:
                log.exception("Failed to build client transfer receipt deal_id=%s", result.deal_id)
            else:
                for chat_id in (result.from_chat_id, result.to_chat_id):
                    try:
                        await self._messenger.send_photo(
                            chat_id,
                            receipt,
                            filename=f"client_transfer_{result.deal_id}.png",
                            caption=f"Перевод #{result.deal_id}",
                        )
                    except Exception:
                        log.exception(
                            "Failed to send client transfer receipt deal_id=%s chat_id=%s",
                            result.deal_id,
                            chat_id,
                        )

        amount = format_amount_core(result.amount, result.precision)
        sender_text = (
            f"Перевод #{result.deal_id} проведён{' (повтор)' if result.repeated else ''}.\n"
            f"Получатель: {escape(result.to_client_name)}\n"
            f"Списано: {amount} {result.currency}\n"
            f"Баланс: {format_amount_core(result.from_balance, result.precision)} {result.currency}"
        )
        try:
            await self._messenger.send(
                result.from_chat_id,
                sender_text,
                reply_to_message_id=reply_to_message_id,
            )
        except Exception:
            log.exception(
                "Failed to notify transfer sender deal_id=%s chat_id=%s",
                result.deal_id,
                result.from_chat_id,
            )
        if result.repeated:
            return
        recipient_text = (
            f"Перевод #{result.deal_id} от {escape(result.from_client_name)}.\n"
            f"Зачислено: {amount} {result.currency}\n"
            f"Баланс: {format_amount_core(result.to_balance, result.precision)} {result.currency}"
        )
        try:
            await self._messenger.send(result.to_chat_id, recipient_text)
        except Exception:
            log.exception(
                "Failed to notify transfer recipient deal_id=%s chat_id=%s",
                result.deal_id,
                result.to_chat_id,
            )


    async def prepare_accounting(self, **command):
        return await self._service.prepare_accounting(**command)

    async def resolve_accounting(self, **command):
        return await self._service.resolve_accounting(**command)

    async def adjust_accounting(self, **command):
        return await self._service.adjust_accounting(**command)

    def accounting_receipt(self, context):
        details = context['details']
        instrument = details['instrument']
        precision = 6 if instrument=='USDT' else 2
        amount = Decimal(details['quantity'])
        status = self._accounting_status(context)
        receipt = self._receipt_builder.build(amount=amount,currency=label_for(instrument),precision=precision,
            rows=(ReceiptRow('Статус',status,accent=True),ReceiptRow('Номер операции',f"#{context['root_id']}"),
                  ReceiptRow('Отправитель',context['source']['name']),ReceiptRow('Получатель',context['target']['name']),
                  ReceiptRow('Дата и время',format_receipt_datetime(context['effective_at'],timezone='Asia/Bangkok'))))
        return receipt, f"Перевод #{context['root_id']}", status


    @staticmethod
    def _accounting_status(context):
        if context['cancelled']:
            return 'Отменён'
        return 'Изменено' if context['root_id']!=context['operation_id'] else 'Проведено'

    def accounting_notice(self, context, side, balance):
        instrument = context['details']['instrument']
        precision = 6 if instrument=='USDT' else 2
        value = format_amount_core(Decimal(context['details']['quantity']),precision)
        direction = 'Списано' if side=='source' else 'Зачислено'
        text = f"Перевод #{context['root_id']}: {self._accounting_status(context).lower()}.\n"
        text += f"Отправитель: {context['source']['name']}\nПолучатель: {context['target']['name']}\n"
        text += f"Сумма: {value} {label_for(instrument)}\n" if context['cancelled'] else f"{direction}: {value} {label_for(instrument)}\n"
        return text+f"Текущий баланс: {format_amount_core(balance,precision)} {label_for(instrument)}"
