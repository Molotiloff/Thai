"""Atomic replay of ordinary wallet/cash movements after an exchange correction.

Posted journals are never edited. Every replay is an exact reversal followed by a
new posting through the ordinary valuation rules, linked to its historical source.
Business allocations which cannot be replayed safely remain explicit blockers.
"""

import hashlib
from dataclasses import replace

from domain.finance.models import ReversalBlocked, canonical_json, decimal
from services.accounting.manual_cash_service import ManualCashService
from services.clients.adjustments import WalletAdjustmentService
from services.clients.conversions import WalletConversionService
from services.clients.transfers import ClientWalletTransferService
from services.clients.wallets import WalletCashService
from services.finance.posting import CallbackDocument, CashTransferService


class SilentOutbox:
    async def enqueue(self, operation):
        # Replay changes valuation, not the fact of a payment or client notification.
        pass


class HistoricalPosting:
    def __init__(self, posting, work, original, correction_key, dependencies):
        self._posting, self._work = posting, work
        self._original, self._key = original, correction_key
        self._dependencies = dependencies

    async def post(self, command, builder, **options):
        import json

        details = json.loads(command.details_json)
        details.update(historical_order=self._original['ordering'],
                       replayed_from=self._original['id'], correction_key=self._key)
        command = replace(command, details_json=canonical_json(details))
        options['work'] = self._work
        document = options.get('document')

        async def save(operation_id, work, plan):
            if document is not None:
                await document.save(operation_id, work, plan)
            for source in self._dependencies:
                await work.journal.add_dependency(source, operation_id)

        options['document'] = CallbackDocument(save)
        return await self._posting.post(command, builder, **options)


class ExchangeHistoryReplay:
    def __init__(self, trading, work, key, actor_id, reason, profit_revision_id):
        self.trading, self.work = trading, work
        self.key = 'history:' + hashlib.sha256(key.encode()).hexdigest()[:32]
        self.actor_id, self.reason = actor_id, reason
        self.profit_revision_id = profit_revision_id
        self.replacements = {}

    def validate(self, rows):
        for row in rows:
            details, deal = row['details'], row['deal']
            supported = (
                (row['kind'] == 'fx_trade' and deal and deal['kind'] == 'fx_trade'
                and not details.get('settlement'))
                or (row['kind'] in ('wallet_deposit', 'wallet_withdrawal')
                and not any(details.get(field) for field in ('request_id', 'payment_watch_id')))
                or row['kind'] == 'wallet_transfer'
                or row['kind'] == 'wallet_conversion'
                or row['kind'] == 'cash_transfer'
                or (row['kind'] == 'manual_journal' and details.get('purpose') in ('wallet_adjustment', 'cash_adjustment'))
            )
            if not supported:
                raise ReversalBlocked(
                    f"Пересчёт затрагивает операцию №{row['id']} ({row['kind']}) со связанным документом. "
                    'Нужна отдельная ревизия этого документа; исходная заявка не изменена.'
                )

    async def rewind(self, rows):
        original_outbox = self.work.outbox
        self.work.outbox = SilentOutbox()
        try:
            # Actual posting order is important for exact inventory snapshot reversal.
            for row in sorted(rows, key=lambda item: item['id'], reverse=True):
                common = self.common(row, 'reverse')
                reason = f"Пересчёт после исправления: {self.reason}"
                if row['kind'] == 'fx_trade':
                    attributes = row['deal']['attributes']
                    contract = attributes.get('contract', attributes)
                    replacement = {name: contract[name] for name in ('mode', 'receive', 'pay')}
                    for name in ('comment', 'presentation'):
                        if name in attributes:
                            replacement[name] = attributes[name]
                    await self.trading.reverse(**common, deal_id=row['deal']['id'],
                        replacement=replacement, reason=reason, work=self.work)
                    current = await self.work.trades.exchange_current(row['deal']['id'])
                    self.replacements[row['id']] = current['id']
                else:
                    common.pop('profit_revision_id')
                    await self.trading._reversals.reverse(**common, original_id=row['id'],
                        reason=reason, details={'profit_revision_id': self.profit_revision_id,
                            'historical_replay': True}, work=self.work)
        finally:
            self.work.outbox = original_outbox

    def common(self, row, suffix):
        return {'key': f"{self.key}:{row['id']}:{suffix}", 'actor_id': self.actor_id,
                'business_date': row['business_date'], 'effective_at': row['effective_at'],
                'profit_revision_id': self.profit_revision_id}

    async def replay(self, rows, original_id, replacement_id):
        result = []
        operation_ids = {original_id: replacement_id}
        original_outbox = self.work.outbox
        self.work.outbox = SilentOutbox()
        try:
            for row in rows:
                common = self.common(row, 'post')
                details = row['details']
                dependencies = tuple(operation_ids.get(source, source) for source in row['dependencies'])
                posting = HistoricalPosting(self.trading._posting, self.work, row, self.key, dependencies)
                if row['kind'] == 'fx_trade':
                    deal_id = self.replacements[row['id']]
                    await self.work.trades.ready(deal_id)
                    operation = await self.trading.post(**common, deal_id=deal_id, work=self.work,
                        historical_order=row['ordering'], replayed_from=row['id'],
                        dependency_operation_ids=dependencies)
                    await self.work.history.exchange_revalued(deal_id, operation.id)
                else:
                    queries = self.work.history.wallet_queries
                    # Transfers/adjustments always use an internal accounting
                    # quote. Recompute that quote from the corrected history;
                    # it is not an agreed execution price to freeze.
                    if row['kind'] in ('wallet_deposit', 'wallet_withdrawal'):
                        service = WalletCashService(posting, queries)
                        method = service.deposit if row['kind'] == 'wallet_deposit' else service.withdraw
                        operation = await method(**common, client_id=details['client'],
                            instrument=details['instrument'], quantity=details['quantity'],
                            quoted_rate=details.get('quoted_rate') if details.get('rate_source') != 'internal' else None,
                            comment=details.get('comment', ''))
                    elif row['kind'] == 'wallet_transfer':
                        operation = await ClientWalletTransferService(posting, queries).transfer(
                            **common, source_client_id=details['source_client'],
                            target_client_id=details['target_client'], instrument=details['instrument'],
                            quantity=details['quantity'], comment=details.get('comment', ''),
                            transfer_root_id=details.get('transfer_root_id', row['id']))
                    elif row['kind'] == 'wallet_conversion':
                        operation = await WalletConversionService(posting, queries, self.trading._queries).transfer(
                            **common, client_id=details['client'], source_instrument=details['source'],
                            target_instrument=details['target'], quantity=details['quantity'],
                            fee=details['fee'],
                            quoted_rate=details.get('rate') if details.get('rate_source') != 'internal' else None,
                            physical=details['physical'])
                    elif row['kind'] == 'cash_transfer':
                        operation = await CashTransferService(posting).transfer(**common,
                            source_id=details['source'], target_id=details['target'],
                            quantity=decimal(details['quantity']))
                    elif details.get('purpose') == 'cash_adjustment':
                        operation = await ManualCashService(posting=posting, queries=self.work.history.cash_queries).adjust(
                            **common, instrument=details['instrument'], quantity=details['quantity'],
                            chat_id=details['chat_id'], comment=details.get('comment', ''))
                    else:
                        operation = await WalletAdjustmentService(posting, queries).adjust(
                            **common, client_id=details['client'], instrument=details['instrument'],
                            quantity=details['quantity'], comment=details.get('comment', ''))
                operation_ids[row['id']] = operation.id
                result.append({'original_operation_id': row['id'], 'operation_id': operation.id})
        finally:
            self.work.outbox = original_outbox
        return result
