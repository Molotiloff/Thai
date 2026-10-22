from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from secrets import randbelow
from typing import cast
from zoneinfo import ZoneInfo

from db_asyncpg.ports.workflows import CashRequestContextRepositoryPort
from domain.deal_policy import exchange_type
from domain.finance.models import FxQuote, InvalidPosting, decimal
from observability import NULL_METRICS, MetricsRecorder
from services.accounting.cash_settlement_service import CashSettlementService
from services.cash_requests.calculator import CashRequestCalculator
from services.cash_requests.card_parser import CashCardParser
from services.cash_requests.card_presenter import CashCardPresenter
from services.cash_requests.create_cash_request import CreateCashRequest
from services.cash_requests.edit_cash_request import EditCashRequest
from services.cash_requests.parsing import ParsedRequest, parse_dep_wd, parse_fx
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from services.cash_requests.schedule_coordinator import CashScheduleCoordinator
from services.clients.ports import WalletReadPort
from services.clients.wallets import WalletCashService
from services.crm.telegram_deal_registrar import TelegramDealRegistrarPort
from services.exchange.ledger import TradingReadPort, TradingUnitOfWork
from services.finance.ports import FinanceUowFactory
from services.metadata_commands import MetadataCommandService


class CashRequestService:
    def __init__(
        self,
        *,
        repo: CashRequestContextRepositoryPort | None = None,
        router_service: RequestRouterService | None = None,
        schedule_service: RequestScheduleService | None = None,
        cmd_map: Mapping[str, tuple[str, str]],
        fx_cmd_map: Mapping[str, tuple[str, str, str]],
        admin_chat_ids: set[int],
        admin_user_ids: set[int],
        deal_registrar: TelegramDealRegistrarPort | None = None,
        calculator: CashRequestCalculator | None = None,
        card_presenter: CashCardPresenter | None = None,
        card_parser: CashCardParser | None = None,
        schedule_coordinator: CashScheduleCoordinator | None = None,
        metrics: MetricsRecorder = NULL_METRICS,
        ledger_uow_factory: FinanceUowFactory | None = None,
        wallet_queries: WalletReadPort | None = None,
        trade_queries: TradingReadPort | None = None,
        wallets: WalletCashService | None = None,
        settlements: CashSettlementService | None = None,
    ) -> None:
        self.cmd_map, self.fx_cmd_map = dict(cmd_map), dict(fx_cmd_map)
        self._ledger_factory = ledger_uow_factory
        if ledger_uow_factory is not None:
            if any(item is None for item in (wallet_queries, trade_queries, wallets)):
                raise ValueError("Accounting requests require wallet and deal ports")
            if fx_cmd_map and settlements is None:
                raise ValueError('Cash exchange commands require a settlement service')
            self._commands = MetadataCommandService(ledger_uow_factory)
            self._wallet_queries, self._queries, self._wallets = wallet_queries, trade_queries, wallets
            self._calculator = CashRequestCalculator(whole_units=False)
            self._settlements = settlements
            return
        if repo is None or router_service is None or schedule_service is None:
            raise ValueError("Legacy requests require repository, router and schedule")
        self.repo = repo
        self.router_service = router_service
        self.schedule_service = schedule_service
        self.cmd_map = dict(cmd_map)
        self.fx_cmd_map = dict(fx_cmd_map)
        self.admin_chat_ids = set(admin_chat_ids)
        self.admin_user_ids = set(admin_user_ids)
        self.create_cash_request = CreateCashRequest(
            repo=repo,
            router_service=router_service,
            schedule_service=schedule_service,
            deal_registrar=deal_registrar,
            calculator=calculator,
            card_presenter=card_presenter,
            card_parser=card_parser,
            schedule_coordinator=schedule_coordinator,
            metrics=metrics,
        )
        self.edit_cash_request = EditCashRequest(
            repo=repo,
            router_service=router_service,
            schedule_service=schedule_service,
            calculator=calculator,
            card_presenter=card_presenter,
            card_parser=card_parser,
            schedule_coordinator=schedule_coordinator,
            metrics=metrics,
        )

    @property
    def supported_commands(self) -> tuple[str, ...]:
        return tuple(set(self.cmd_map.keys()) | set(self.fx_cmd_map.keys()))

    def help_text(self) -> str:
        if self._ledger_factory is not None:
            return (
                "• /депбат <сумма/expr> [Принимает] [Выдает] [! комментарий]\n"
                "• /выдбат <сумма/expr> [Выдает] [Принимает] [! комментарий]\n"
                "• /пбатвт <бат> <USDT> [Кассир] [Клиент] [! комментарий]\n"
                "Суффиксы валют: бат, безнал, т, д, б, е, п, р.\n"
                "Сначала нажмите «Готово к расчету», затем проведите фактическую сумму:\n"
                "• внесение: /бат 1000 Б-000001;\n"
                "• выдача: /бат -1000 Б-000001.\n"
                "Кассовый обмен: подтвердите обе стороны; касса изменится одной операцией.\n"
                "Для редактирования новой заявки ответьте командой на карточку бота."
            )
        cities = (
            ", ".join(sorted(self.router_service.city_keys))
            if self.router_service.city_keys
            else "—"
        )
        return (
            "Форматы:\n"
            "• /депр [город] <сумма/expr> [Принимает] [Выдает] [! комментарий]\n"
            "• /выдр [город] <сумма/expr> [Выдает] [Принимает] [! комментарий]\n"
            "• /првд [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n"
            "• /пдвр [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n"
            "• /прве [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n\n"
            f"Города: {cities}\n"
            f"Если город не указан — по умолчанию: {self.router_service.default_city}\n\n"
            "Расчет в городской кассе:\n"
            "• сначала нажмите «Готово к расчету» в карточке;\n"
            "• затем укажите номер заявки в кассовой команде, например "
            "/usd -125 Б-123456.\n\n"
            "Редактирование:\n"
            "• ответьте командой на карточку БОТА — можно менять сумму, город, контакты, комментарий;\n"
            "• тип и валюты менять нельзя;\n"
            "• проведённые и отменённые заявки редактировать нельзя."
        )

    def parse_command(self, raw_text: str) -> ParsedRequest | None:
        parsed: ParsedRequest | None = parse_fx(
            raw_text,
            fx_cmd_map=self.fx_cmd_map,
            city_keys=set() if self._ledger_factory else self.router_service.city_keys,
            default_city="" if self._ledger_factory else self.router_service.default_city,
        )
        if not parsed:
            parsed = parse_dep_wd(
                raw_text,
                cmd_map=self.cmd_map,
                city_keys=set() if self._ledger_factory else self.router_service.city_keys,
                default_city="" if self._ledger_factory else self.router_service.default_city,
            )
        return parsed

    @staticmethod
    def deal_id(request_id):
        import re
        match = re.fullmatch(r'Б-(\d+)', str(request_id))
        if match is None or int(match[1]) <= 0:
            raise InvalidPosting('Нужен номер заявки Б-000001')
        return int(match[1])

    @staticmethod
    def _contract_values(details):
        if details.money is not None:
            return {'request_kind':'deposit' if str(details.kind)=='dep' else 'withdrawal',
                'instrument':str(details.money.currency),'quantity':str(details.money.amount)}
        receive,pay = details.receive,details.pay
        return {'request_kind':'cash_exchange','operation_type':exchange_type(str(receive.currency),str(pay.currency)),
            'receive':{'instrument':str(receive.currency),'quantity':str(receive.amount),'precision':receive.precision,'quoted_rate':None},
            'pay':{'instrument':str(pay.currency),'quantity':str(pay.amount),'precision':pay.precision,'quoted_rate':None}}

    async def create_request(self, *, key, actor_id, client_id, parsed, client_name, creator_name,
                             source_chat_id=None, quoted_rates=None):
        if parsed.kind not in ('dep', 'wd','fx'):
            raise InvalidPosting('Ожидается заявка на внесение, выдачу или кассовый обмен')
        accounts = [{**row, 'currency_code':row['instrument_code']}
                    for row in await self._wallet_queries.wallets(client_id)]
        details = self._calculator.calculate(parsed, accounts)
        kind = {'dep':'deposit','wd':'withdrawal','fx':'cash_exchange'}[parsed.kind]
        payload = {'client_id':client_id, **self._contract_values(details),
            'contact1':parsed.contact1,'contact2':parsed.contact2,
            'comment':parsed.comment,'source_chat_id':source_chat_id}
        if parsed.kind=='fx' and quoted_rates:
            currencies = await self._queries.accounts(client_id,parsed.in_code,parsed.out_code,'cash')
            for side,rate in quoted_rates.items():
                if rate is not None:
                    currency = currencies[side]['currency']
                    quote = FxQuote(currency,decimal(rate),'units_per_usdt' if currency=='THB' else 'usdt_per_unit',payload[side]['instrument'])
                    payload[side]['quoted_rate'] = quote.quoted_rate
        async def action(work):
            uow = cast(TradingUnitOfWork, work)
            await uow.clients.lock_client(client_id)
            attrs = {**payload, **({'precision':details.money.precision} if details.money else {}), 'revision':1, 'mode':'cash',
                'client_name':client_name, 'creator_name':creator_name,
                'pin_code':f'{randbelow(900)+100}-{randbelow(900)+100}'}
            result = await uow.trades.create(actor_id, client_id, attrs, key, kind=kind)
            await uow.trades.cash_request_changed(result['deal_id'])
            return result
        result = await self._commands.execute(key=key, actor_id=actor_id, kind='cash_request_create',
            payload=payload, action=action, permission='deal.post')
        return await self._queries.detail(result['deal_id'])

    async def edit_request(self, *, key, actor_id, request_id, parsed, editor_name, effective_at):
        deal_id = self.deal_id(request_id)
        previous = await self._queries.detail(deal_id)
        attrs = previous['attributes']
        if previous['kind'] != {'dep':'deposit','wd':'withdrawal','fx':'cash_exchange'}.get(parsed.kind):
            raise InvalidPosting('Нельзя менять тип заявки')
        accounts = [{**row, 'currency_code':row['instrument_code']}
                    for row in await self._wallet_queries.wallets(previous['client_id'])]
        codes = (attrs['receive']['instrument'],attrs['pay']['instrument']) if parsed.kind=='fx' else (attrs['instrument'],)
        details = self._calculator.calculate(parsed, accounts, expected_codes=codes)
        values = self._contract_values(details)
        if not details.money:
            for side in ('receive','pay'):
                values[side]['quoted_rate'] = attrs[side].get('quoted_rate')
        payload = {'deal_id':deal_id, **({'quantity':values['quantity']} if details.money else values),
            'contact1':parsed.contact1, 'contact2':parsed.contact2, 'comment':parsed.comment}
        async def action(work):
            uow = cast(TradingUnitOfWork, work)
            await uow.clients.lock_client(previous['client_id'])
            changed = {**attrs, **{k:v for k,v in payload.items() if k!='deal_id'},
                'revision':attrs['revision']+1, 'editor_name':editor_name,
                'changed_at':effective_at.astimezone(ZoneInfo('Asia/Bangkok')).strftime('%Y-%m-%d %H:%M')}
            await uow.trades.replace_cash_request(deal_id, changed, attrs['revision'])
            await uow.trades.cash_request_changed(deal_id)
            return {'deal_id':deal_id}
        await self._commands.execute(key=key, actor_id=actor_id, kind='cash_request_edit',
            payload=payload, action=action, permission='deal.post')
        return await self._queries.detail(deal_id)

    async def change_request_status(self, *, key, actor_id, request_id, action, expected_revision=None, reason="Отменена менеджером"):
        deal_id = self.deal_id(request_id)
        async def change(work):
            uow = cast(TradingUnitOfWork, work)
            deal = await uow.trades.lock(deal_id)
            if deal['kind'] not in ('deposit', 'withdrawal','cash_exchange'):
                raise InvalidPosting('Это не кассовая заявка')
            if expected_revision is not None and deal['attributes']['revision'] != expected_revision:
                raise InvalidPosting('Заявка изменена; используйте обновлённую карточку')
            if action == 'ready':
                result = await uow.trades.ready(deal_id)
            elif action == 'cancel':
                result = await uow.trades.cancel_draft(deal_id, reason)
            elif action == 'done' and deal['status']=='posted':
                result = {'deal_id':deal_id, 'status':'posted'}
            else:
                raise InvalidPosting('Сначала проведите фактический расчёт кассовой командой с номером заявки')
            await uow.trades.cash_request_changed(deal_id)
            return result
        return await self._commands.execute(key=key, actor_id=actor_id, kind='cash_request_status',
            payload={'deal_id':deal_id, 'action':action, 'revision':expected_revision, 'reason':reason if action=='cancel' else None},
            action=change, permission='deal.post')

    async def settle_request(self, *, request_id, instrument, signed_quantity, quoted_rate=None, profit_revision_id=None, **common):
        deal_id = self.deal_id(request_id)
        deal = await self._queries.detail(deal_id)
        if deal['kind']=='cash_exchange':
            return await self._settlements.confirm_exchange(**common,request_id=deal_id,instrument=instrument,
                signed_quantity=signed_quantity,quoted_rate=quoted_rate,profit_revision_id=profit_revision_id)
        if deal['kind'] not in ('deposit','withdrawal'):
            raise InvalidPosting('Это не кассовая заявка')
        attrs = deal['attributes']
        signed = decimal(signed_quantity)
        expected = decimal(attrs['quantity']) * (1 if deal['kind']=='deposit' else -1)
        if instrument != attrs['instrument'] or signed != expected:
            raise InvalidPosting('Валюта, сумма или знак не совпадают с заявкой')
        if deal['effective_status']=='posted':
            async with self._ledger_factory() as work:
                uow = cast(TradingUnitOfWork, work)
                await uow.clients.guard_directory(common['actor_id'], common['key'], 'finance.post')
                return await uow.journal.get(deal['operation_id'])
        if deal['effective_status']!='ready':
            raise InvalidPosting('Сначала отметьте заявку готовой к расчёту')
        method = self._wallets.deposit if deal['kind']=='deposit' else self._wallets.withdraw
        return await method(**common, client_id=deal['client_id'], instrument=instrument,
            quantity=abs(signed), comment=attrs.get('comment',''), request_id=deal_id)

    def card_plan(self, deal, keyboards):
        from domain import CashRequestKind, Money
        from services.cash_requests.audit import RequestAudit, audit_lines_for_request_chat
        from services.cash_requests.deal_status_workflow import (
            CANCEL_POLICY,
            DONE_POLICY,
            READY_POLICY,
            CashDealStatusCardPresenter,
        )
        from services.cash_requests.workflow_models import CashRequestDetails
        attrs = deal['attributes']
        request_id = f"Б-{deal['id']:06d}"
        state = deal['effective_status']
        if deal['kind']=='cash_exchange':
            details = CashRequestDetails(kind=CashRequestKind.EXCHANGE,
                receive=Money(Decimal(attrs['receive']['quantity']),attrs['receive']['instrument'],attrs['receive']['precision']),
                pay=Money(Decimal(attrs['pay']['quantity']),attrs['pay']['instrument'],attrs['pay']['precision']))
        else:
            details = CashRequestDetails(kind=CashRequestKind.DEPOSIT if deal['kind']=='deposit' else CashRequestKind.WITHDRAWAL,
                money=Money(Decimal(attrs['quantity']), attrs['instrument'], attrs['precision']))
        plan = CashCardPresenter(keyboards).build(details=details,
            request_id=request_id, city='', client_name=attrs['client_name'], pin_code=attrs['pin_code'],
            contact1=attrs['contact1'], contact2=attrs['contact2'], comment=attrs['comment'],
            audit_lines=audit_lines_for_request_chat(RequestAudit(attrs['creator_name'],
                attrs.get('editor_name'), attrs.get('changed_at'))), changed=attrs['revision']>1)
        markup = keyboards.deal_actions(request_id=f"{request_id}:{attrs['revision']}") if state=='draft' else (
            keyboards.completion_action(request_id=f"{request_id}:{attrs['revision']}") if state=='ready' else None)
        if deal['kind']=='cash_exchange' and deal.get('cash_confirmations'):
            from dataclasses import replace

            from services.number_formatting import format_amount_core
            from services.wallets.compact_formatter import label_for
            lines = ['','Подтверждено:']
            for leg in deal['cash_confirmations']:
                quantity = format_amount_core(leg['quantity'],attrs[leg['direction']]['precision'])
                lines.append(f"{'Принимаем' if leg['direction']=='receive' else 'Выдаем'}: {quantity} {label_for(leg['instrument_code'])}")
            if state=='ready':
                lines.append('Касса изменится после подтверждения обеих сторон.')
            suffix = '\n'.join(lines)
            plan = replace(plan,client_text=plan.client_text+suffix,request_text=plan.request_text+suffix)
        policy = {'ready':READY_POLICY,'posted':DONE_POLICY,'cancelled':CANCEL_POLICY}.get(state)
        from dataclasses import replace
        if policy is not None:
            cards = CashDealStatusCardPresenter()
            at = deal.get('status_at') or deal['created_at']
            if isinstance(at, str):
                at = datetime.fromisoformat(at)
            at = at.astimezone(ZoneInfo('Asia/Bangkok'))
            return replace(plan, client_text=cards.apply(plan.client_text, policy, at=at),
                request_text=cards.apply(plan.request_text, policy, at=at), request_markup=markup)
        return replace(plan, request_markup=markup)
