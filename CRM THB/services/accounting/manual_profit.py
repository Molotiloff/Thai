"""Audited historical P&L without inventing cash, wallet or trading volumes."""

import hashlib
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from domain.finance.models import (
    InvalidPosting,
    JournalLine,
    LedgerContext,
    OperationCommand,
    PostingPlan,
    Side,
    Valuation,
    canonical_json,
    decimal,
    exact,
    revision_details,
)
from services.finance.posting import CallbackDocument, OperationPostingService, ReversalService
from services.metadata_commands import MetadataCommandService


class ManualProfitService:
    def __init__(self, factory):
        self._commands = MetadataCommandService(factory)
        self._posting = OperationPostingService(factory)
        self._reversals = ReversalService(factory)

    @staticmethod
    def attributes(amount_usdt, thb_amount, comment):
        amount = exact(decimal(amount_usdt), 8)
        if not amount:
            raise InvalidPosting('Сумма прибыли или убытка должна отличаться от нуля')
        thb = None if thb_amount is None else exact(decimal(thb_amount), 2)
        if thb is not None and thb <= 0:
            raise InvalidPosting('Количество батов должно быть положительным')
        return {'amount_usdt': amount, 'thb_amount': thb, 'comment': comment.strip()}

    @staticmethod
    def dates(effective_at, business_date):
        at = effective_at or datetime.now(UTC)
        if at.tzinfo is None or at.utcoffset() is None:
            raise InvalidPosting('Время должно содержать часовой пояс')
        return at, business_date or at.astimezone(ZoneInfo('Asia/Bangkok')).date()

    async def _post(self, work, *, key, actor_id, client_id, attributes, effective_at,
                    business_date, profit_revision_id, deal_id=None):
        if client_id is not None:
            await work.clients.lock_client(client_id)
        attributes = {**attributes, 'business_date': business_date}
        accounts = await work.manual_profit.accounts()
        positive = attributes['amount_usdt'] > 0
        result_account = accounts['manual_profit_income' if positive else 'manual_profit_loss']
        offset = accounts['historical_profit_offset']
        if deal_id is None:
            deal_id = await work.manual_profit.create(actor_id, client_id, attributes, key)
        command = OperationCommand(key, 'manual_profit', actor_id, business_date, effective_at,
            tuple(sorted((result_account, offset))), canonical_json(revision_details({
                'deal_id': deal_id, 'client_id': client_id, 'attributes': attributes}, profit_revision_id)),
            context=LedgerContext.PROFIT)
        value = abs(attributes['amount_usdt'])
        side = Side.CREDIT if positive else Side.DEBIT
        plan = PostingPlan((JournalLine(result_account, 'USDT', side, value, value, Valuation.BASE),
                            JournalLine(offset, 'USDT', side.opposite, value, value, Valuation.BASE)))

        async def save(operation_id, transaction, plan):
            await transaction.manual_profit.post(deal_id, operation_id)

        posted = await self._posting.post(command, lambda state: plan, work=work, document=CallbackDocument(save))
        return {'deal_id': deal_id, 'operation_id': posted.id, 'business_date': business_date.isoformat()}

    async def record(self, *, key, actor_id, amount_usdt, client_id=None, thb_amount=None,
                     comment='', business_date=None, effective_at=None, profit_revision_id=None,
                     client_reference=None):
        if client_reference is not None and client_id is not None:
            raise InvalidPosting('Укажите имя либо ID клиента, а не оба способа сразу')
        attributes = self.attributes(amount_usdt, thb_amount, comment)
        posting_key = 'profit:' + hashlib.sha256(key.encode()).hexdigest()
        payload = {**attributes, 'client_id': client_id, 'business_date': business_date,
                   'effective_at': effective_at, 'profit_revision_id': profit_revision_id, 'type': 'manual_profit'}
        if client_reference is not None:
            payload['client_reference'] = client_reference

        async def action(work):
            await work.journal.lock_posting_period(profit_revision_id=profit_revision_id)
            selected_client = (await work.clients.resolve_profit_client(client_reference)
                               if client_reference is not None else client_id)
            at, day = self.dates(effective_at, business_date)
            result = await self._post(work, key=posting_key, actor_id=actor_id, client_id=selected_client,
                attributes=attributes, effective_at=at, business_date=day, profit_revision_id=profit_revision_id)
            return {**result, 'request_id': result['deal_id']}

        return await self._commands.execute(key=key, actor_id=actor_id, kind='trade_create',
            payload=payload, action=action, permission='deal.post')

    async def change(self, *, key, actor_id, deal_id, reason, expected_deal_id=None, replacement=None,
                     effective_at=None, business_date=None, profit_revision_id=None):
        if not reason.strip():
            raise InvalidPosting('Укажите причину изменения')
        attributes = None if replacement is None else self.attributes(
            replacement['amount_usdt'], replacement.get('thb_amount'), replacement.get('comment', ''))
        posting_key = 'profit:' + hashlib.sha256(key.encode()).hexdigest()
        payload = {'deal_id': deal_id, 'expected_deal_id': expected_deal_id, 'replacement': replacement,
                   'reason': reason, 'effective_at': effective_at, 'business_date': business_date,
                   'profit_revision_id': profit_revision_id, 'type': 'manual_profit'}

        async def action(work):
            await work.journal.lock_posting_period(profit_revision_id=profit_revision_id)
            current = await work.manual_profit.current(deal_id)
            if current['cancelled'] or current['id'] != (expected_deal_id or deal_id):
                raise InvalidPosting('Запись уже изменена или отменена; обновите карточку')

            async def prepare(transaction):
                await transaction.manual_profit.current(current['id'])

            replacement_id = None
            if attributes is not None:
                at, day = self.dates(effective_at, business_date)
                replacement_id = await work.manual_profit.create(actor_id, replacement.get('client_id'),
                    {**attributes, 'business_date': day}, posting_key)

            async def save_reversal(operation_id, transaction, plan):
                await transaction.manual_profit.reversed(current['id'], operation_id, reason, replacement_id)

            # Remove the old P&L from its own month, even when corrected today.
            reversed_operation = await self._reversals.reverse(original_id=current['operation_id'],
                key=posting_key+':reverse', actor_id=actor_id, business_date=current['business_date'],
                effective_at=current['effective_at'], reason=reason, prepare=prepare, work=work,
                details=revision_details({'deal_id': current['id']}, profit_revision_id),
                document=CallbackDocument(save_reversal))
            result = None
            if attributes is not None:
                result = await self._post(work, key=posting_key, actor_id=actor_id,
                    client_id=replacement.get('client_id'), attributes=attributes, effective_at=at,
                    business_date=day, profit_revision_id=profit_revision_id, deal_id=replacement_id)
            return {**(result or {'deal_id': current['id']}), 'request_id': current['root_id'],
                    'reversal_operation_id': reversed_operation.id}

        return await self._commands.execute(key=key, actor_id=actor_id,
            kind='trade_correct' if replacement is not None else 'trade_cancel',
            payload=payload, action=action, permission='finance.reverse')
