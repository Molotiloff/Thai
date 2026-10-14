from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TypedDict
from zoneinfo import ZoneInfo

from db_asyncpg.ports.workflows import ExchangeCommandRepositoryPort
from domain.deal_policy import BASE_INSTRUMENTS
from domain.finance.models import InvalidPosting
from observability import NULL_METRICS, MetricsRecorder
from services.act_counter import ActCounterService
from services.cash_requests.constants import ACCOUNTING_CASH_SUFFIXES
from services.clients.ports import WalletReadPort
from services.crm.telegram_deal_registrar import TelegramDealRegistrarPort
from services.exchange.balance_service import ExchangeBalanceService
from services.exchange.calculator import ExchangeCalculator
from services.exchange.cancel_exchange_request import CancelExchangeRequest
from services.exchange.card_parser import extract_created_by, extract_request_id
from services.exchange.create_exchange_request import (
    CreateExchangeParams,
    CreateExchangeRequest,
    CreateExchangeResult,
)
from services.exchange.edit_exchange_request import EditExchangeParams, EditExchangeRequest
from services.exchange.keyboard_port import ExchangeKeyboardPort
from services.exchange.ledger import TradingReadPort
from services.exchange.notification_builder import ExchangeNotificationBuilder
from services.exchange.source_link_service import ExchangeSourceLinkService
from services.exchange.text_builder import ExchangeTextBuilder
from services.exchange.transaction_service import ExchangeTransactionService, TradingService
from services.exchange.wallet_presenter import ExchangeWalletPresenter
from services.messaging import MessengerPort, ReplierPort
from services.unit_of_work import UnitOfWorkFactory


class _ExchangeUseCaseOptions(TypedDict):
    repo: ExchangeCommandRepositoryPort
    request_chat_id: int | None
    balance_service: ExchangeBalanceService
    calculator: ExchangeCalculator
    text_builder: ExchangeTextBuilder
    unit_of_work_factory: UnitOfWorkFactory
    keyboards: ExchangeKeyboardPort
    act_counter_service: ActCounterService | None
    deal_registrar: TelegramDealRegistrarPort | None
    transaction_service: ExchangeTransactionService
    wallet_presenter: ExchangeWalletPresenter
    notification_builder: ExchangeNotificationBuilder
    source_links: ExchangeSourceLinkService
    metrics: MetricsRecorder


@dataclass(slots=True, frozen=True)
class ParsedAcceptShortCommand:
    recv_code: str
    recv_amount_expr: str
    pay_code: str
    pay_amount_expr: str
    user_note: str | None
    recv_amount: Decimal
    pay_amount: Decimal
    recv_prec: int
    pay_prec: int
    rate_str: str


@dataclass(slots=True, frozen=True)
class ExchangeReplyContext:
    message_id: int
    text: str
    authored_by_bot: bool


@dataclass(slots=True, frozen=True)
class AcceptShortCommand:
    chat_id: int
    chat_name: str
    message_id: int
    text: str
    actor_name: str
    reply: ExchangeReplyContext | None = None


class AcceptShortService:
    ACCOUNTING_RECEIVE = {'п'+suffix:code for suffix,code in ACCOUNTING_CASH_SUFFIXES.items()}
    ACCOUNTING_PAY = {'о'+suffix:code for suffix,code in ACCOUNTING_CASH_SUFFIXES.items()}
    RECV_MAP = {
        "пбат": "THB",
        "пд": "USD",
        "пе": "EUR",
        "пт": "USDT",
        "пр": "RUB",
        "пб": "USDW",
        "прмск": "РУБМСК",
        "прспб": "РУБСПБ",
        "прпер": "РУБПЕР",
        "пп": "EUR500",
    }
    PAY_MAP = {
        "обат": "THB",
        "од": "USD",
        "ое": "EUR",
        "от": "USDT",
        "ор": "RUB",
        "об": "USDW",
        "ормск": "РУБМСК",
        "орспб": "РУБСПБ",
        "орпер": "РУБПЕР",
        "оп": "EUR500",
    }
    RUB_CODES = {"RUB", "РУБМСК", "РУБСПБ", "РУБПЕР"}
    _COMMAND_RE = re.compile(
        r"^/(пбат|пд|пе|пт|пр|пб|прмск|прспб|прпер|пп)(?:@\w+)?\s+(.+?)\s+"
        r"(обат|од|ое|от|ор|об|ормск|орспб|орпер|оп)\s+(\S+)(?:\s+(.+))?$",
        flags=re.IGNORECASE | re.UNICODE,
    )

    def __init__(
        self,
        repo: ExchangeCommandRepositoryPort | None = None,
        request_chat_id: int | None = None,
        act_counter_service: ActCounterService | None = None,
        deal_registrar: TelegramDealRegistrarPort | None = None,
        unit_of_work_factory: UnitOfWorkFactory | None = None,
        balance_service: ExchangeBalanceService | None = None,
        calculator: ExchangeCalculator | None = None,
        text_builder: ExchangeTextBuilder | None = None,
        transaction_service: ExchangeTransactionService | None = None,
        source_links: ExchangeSourceLinkService | None = None,
        notification_builder: ExchangeNotificationBuilder | None = None,
        wallet_presenter: ExchangeWalletPresenter | None = None,
        keyboards: ExchangeKeyboardPort | None = None,
        metrics: MetricsRecorder = NULL_METRICS,
        accounting_trading: TradingService | None = None,
        wallet_queries: WalletReadPort | None = None,
        trading_queries: TradingReadPort | None = None,
    ) -> None:
        if accounting_trading is not None:
            if wallet_queries is None or trading_queries is None or source_links is None:
                raise ValueError('Accounting exchanges require read ports and source links')
            self._trading, self._wallet_queries, self._queries = accounting_trading, wallet_queries, trading_queries
            self.source_links = source_links
            return
        self.repo = repo
        self.request_chat_id = request_chat_id
        if unit_of_work_factory is None:
            raise ValueError("unit_of_work_factory is required")
        if keyboards is None:
            raise ValueError("keyboards is required")
        balance_service = balance_service or ExchangeBalanceService(unit_of_work_factory)
        calculator = calculator or ExchangeCalculator()
        text_builder = text_builder or ExchangeTextBuilder()
        transaction_service = transaction_service or ExchangeTransactionService(
            unit_of_work_factory=unit_of_work_factory,
            balance_service=balance_service,
        )
        if source_links is None:
            raise ValueError("source_links is required")
        common: _ExchangeUseCaseOptions = {
            "repo": repo,
            "request_chat_id": request_chat_id,
            "balance_service": balance_service,
            "calculator": calculator,
            "text_builder": text_builder,
            "unit_of_work_factory": unit_of_work_factory,
            "act_counter_service": act_counter_service,
            "deal_registrar": deal_registrar,
            "transaction_service": transaction_service,
            "source_links": source_links,
            "notification_builder": notification_builder or ExchangeNotificationBuilder(),
            "wallet_presenter": wallet_presenter or ExchangeWalletPresenter(),
            "keyboards": keyboards,
            "metrics": metrics,
        }
        self.create_exchange_request = CreateExchangeRequest(**common)
        self.edit_exchange_request = EditExchangeRequest(**common)
        self.cancel_exchange_request = CancelExchangeRequest(**common)

    async def _parse_accounting(self, text, client_id):
        accounts = [{**row,'currency_code':row['instrument_code']}
                    for row in await self._wallet_queries.wallets(client_id)]
        try:
            parsed = self.parse_text(text, accounts=accounts, receive_map=self.ACCOUNTING_RECEIVE,
                pay_map=self.ACCOUNTING_PAY, base_codes=BASE_INSTRUMENTS)
        except ValueError as error:
            raise InvalidPosting(str(error)) from error
        if parsed is None:
            raise InvalidPosting('Пример обмена: /пбат 3500 от 100 [комментарий]')
        return parsed

    @staticmethod
    def _presentation(parsed, *, client_name, creator_name, source_chat_id=None, changed_at=None):
        result = {'client_name':client_name, 'creator_name':creator_name, 'rate':parsed.rate_str,
            'recv_prec':parsed.recv_prec, 'pay_prec':parsed.pay_prec, 'note':parsed.user_note,
            'formula':parsed.pay_amount_expr, 'source_chat_id':source_chat_id}
        if changed_at is not None:
            result['changed_at'] = changed_at.astimezone(ZoneInfo('Asia/Bangkok')).strftime('%Y-%m-%d %H:%M')
        return result

    async def create_accounting(self, *, text, client_id, common, chat_id, chat_name, actor_name):
        parsed = await self._parse_accounting(text,client_id)
        return await self._create_accounting(parsed=parsed,client_id=client_id,common=common,
            chat_id=chat_id,chat_name=chat_name,actor_name=actor_name)

    async def create_from_crm(self, *, client_id, receive, pay, common, client_name, actor_name,
                              comment='', profit_revision_id=None):
        accounts = await self._queries.accounts(client_id,receive['instrument'],pay['instrument'],'wallet')
        calc = ExchangeCalculator(base_codes=BASE_INSTRUMENTS).calculate(
            recv_code=receive['instrument'],pay_code=pay['instrument'],
            recv_amount_expr=str(receive['quantity']),pay_amount_expr=str(pay['quantity']),
            accounts=[{'currency_code':leg['instrument'],'precision':accounts[side]['precision']}
                for side,leg in (('receive',receive),('pay',pay))])
        parsed = ParsedAcceptShortCommand(calc.recv_code,str(receive['quantity']),calc.pay_code,
            str(pay['quantity']),comment,calc.recv_amount,calc.pay_amount,calc.recv_precision,
            calc.pay_precision,calc.rate_text)
        return await self._create_accounting(parsed=parsed,client_id=client_id,common=common,
            chat_id=None,chat_name=client_name,actor_name=actor_name,receive=receive,pay=pay,
            profit_revision_id=profit_revision_id)

    async def _create_accounting(self, *, parsed, client_id, common, chat_id, chat_name, actor_name,
                                 receive=None, pay=None, profit_revision_id=None):
        presentation = self._presentation(parsed, client_name=chat_name or f'Клиент {client_id}',
            creator_name=actor_name, source_chat_id=chat_id)
        stored = await self._queries.exchange_by_reference(common['key'])
        if stored is not None:
            presentation = stored['attributes'].get('contract',stored['attributes']).get('presentation')
        deal_id = await self._trading.create_and_post(**common,client_id=client_id,
            receive=receive or {'instrument':parsed.recv_code,'quantity':parsed.recv_amount},
            pay=pay or {'instrument':parsed.pay_code,'quantity':parsed.pay_amount},
            comment=parsed.user_note or '',presentation=presentation,profit_revision_id=profit_revision_id)
        return await self.source_links.accounting_context(deal_id)

    async def edit_accounting(self, *, text, deal_id, common, actor_name):
        context = await self.source_links.accounting_context(deal_id)
        root,active = context['root'],context['active']
        if root['attributes']['mode']!='wallet':
            raise InvalidPosting('Кассовый обмен исправляется в CRM с проверкой кассы')
        parsed = await self._parse_accounting(text,root['client_id'])
        return await self.correct_accounting(deal_id=deal_id,common=common,actor_name=actor_name,
            replacement={'mode':'wallet','receive':{'instrument':parsed.recv_code,'quantity':parsed.recv_amount},
                'pay':{'instrument':parsed.pay_code,'quantity':parsed.pay_amount},'comment':parsed.user_note or ''},
            expected_deal_id=active['id'],reason='Редактирование обменной заявки в Telegram',parsed=parsed)

    async def correct_accounting(self, *, deal_id, common, actor_name, replacement, expected_deal_id,
                                 reason, parsed=None, profit_revision_id=None):
        context = await self.source_links.accounting_context(deal_id)
        root = context['root']
        if replacement['mode']!=root['attributes']['mode']:
            raise InvalidPosting('Нельзя менять способ учёта обмена при исправлении')
        if parsed is None:
            receive,pay = replacement['receive'],replacement['pay']
            accounts = await self._queries.accounts(root['client_id'],receive['instrument'],pay['instrument'],replacement['mode'])
            calc = ExchangeCalculator(base_codes=BASE_INSTRUMENTS).calculate(
                recv_code=receive['instrument'],pay_code=pay['instrument'],recv_amount_expr=str(receive['quantity']),
                pay_amount_expr=str(pay['quantity']),accounts=[{'currency_code':leg['instrument'],'precision':accounts[side]['precision']}
                    for side,leg in (('receive',receive),('pay',pay))])
            parsed = ParsedAcceptShortCommand(calc.recv_code,str(receive['quantity']),calc.pay_code,str(pay['quantity']),
                replacement.get('comment'),calc.recv_amount,calc.pay_amount,calc.recv_precision,calc.pay_precision,calc.rate_text)
        original = root['attributes'].get('presentation',{})
        presentation = self._presentation(parsed,client_name=original.get('client_name') or context.get('client_name') or 'Общая касса',
            creator_name=original.get('creator_name') or actor_name,
            source_chat_id=original.get('source_chat_id'),changed_at=common['effective_at'])
        return await self._trading.correct(**common,deal_id=root['id'],expected_deal_id=expected_deal_id,
            replacement={**replacement,'presentation':presentation},reason=reason,profit_revision_id=profit_revision_id)

    async def cancel_accounting(self, *, deal_id, expected_deal_id, common, reason):
        context = await self.source_links.accounting_context(deal_id)
        active = context['active']
        if active['id']!=expected_deal_id:
            raise InvalidPosting('Заявка изменена; используйте обновлённую карточку')
        if active['effective_status']=='cancelled':
            return {'request_id':context['root']['id']}
        if active['operation_id'] is None:
            await self._trading.cancel_draft(key=common['key'],actor_id=common['actor_id'],deal_id=active['id'],reason=reason)
        else:
            await self._trading.reverse(**common,deal_id=active['id'],reason=reason)
        return {'request_id':context['root']['id']}

    async def accounting_texts(self, context, *, accounts=None):
        root,active = context['root'],context['active']
        attrs = active['attributes']
        presentation = attrs.get('presentation',{})
        receive,pay = attrs['receive'],attrs['pay']
        if accounts is None:
            accounts = await self._queries.accounts(active['client_id'],receive['instrument'],pay['instrument'],attrs['mode'])
        rate = presentation.get('rate')
        if rate is None:
            rate = ExchangeCalculator(base_codes=BASE_INSTRUMENTS).calculate(
                recv_code=receive['instrument'],pay_code=pay['instrument'],recv_amount_expr=str(receive['quantity']),
                pay_amount_expr=str(pay['quantity']),accounts=[{'currency_code':leg['instrument'],'precision':accounts[side]['precision']}
                    for side,leg in (('receive',receive),('pay',pay))]).rate_text
        texts = ExchangeTextBuilder.build_new_texts(req_id=root['id'],table_req_id=root['id'],
            client_name=presentation.get('client_name') or context.get('client_name') or 'Общая касса',
            recv_code=receive['instrument'],recv_amount=Decimal(str(receive['quantity'])),recv_prec=accounts['receive']['precision'],
            pay_code=pay['instrument'],pay_amount=Decimal(str(pay['quantity'])),pay_prec=accounts['pay']['precision'],
            rate=rate,creator_name=presentation.get('creator_name') or 'CRM',note=attrs.get('comment'),
            formula=presentation.get('formula') or str(pay['quantity']),changed_at=presentation.get('changed_at'))
        if active['effective_status']=='cancelled':
            at = context['status_at'] or active['created_at']
            return replace(texts,client_text=ExchangeNotificationBuilder.cancelled_client_card(texts.client_text,
                cancelled_at=at.astimezone(ZoneInfo('Asia/Bangkok'))),
                request_text=ExchangeNotificationBuilder.cancelled_request_card(texts.request_text))
        return texts

    def is_request_chat_origin(self, chat_id: int) -> bool:
        return bool(self.request_chat_id and int(chat_id) == int(self.request_chat_id))

    async def create_request(
        self,
        params: CreateExchangeParams,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
    ) -> CreateExchangeResult:
        """Apply the same client/request-chat ledger policy for Telegram and CRM."""
        is_request_origin = self.is_request_chat_origin(params.chat_id)
        return await self.create_exchange_request.execute_core(
            replace(
                params,
                recv_is_deposit=is_request_origin,
                pay_is_withdraw=is_request_origin,
            ),
            messenger=messenger,
            replier=replier,
        )

    @classmethod
    def help_text(cls) -> str:
        return (
            "Формат:\n"
            "  /пд|/пе|/пт|/пр|/пб|/пбат <сумма/expr> "
            "<од|ое|от|ор|об|обат> <сумма/expr> [комментарий]\n\n"
            "Примеры:\n"
            "• /пд 1000 ое 1000/0.92 Клиент Петров\n"
            "• /пе (2500+500) ор 300000 «наличные»\n"
            "• /пт 700 од 700*1.08 срочно\n"
            "• /пр 100000 от 100000/94 договор №42\n"
            "• /пр 1000 обат 1000/2.7\n"
            "• /пбат 1000 ор 1000*2.7"
        )

    @classmethod
    def parse_text(cls, raw, *, accounts, receive_map=None, pay_map=None, base_codes=None):
        """One parser/calculator for the Telegram interface, independent of storage."""
        match = re.fullmatch(r"/(\w+)(?:@\w+)?\s+(.+?)\s+(\w+)\s+(\S+)(?:\s+(.+))?", raw.strip(), re.IGNORECASE)
        receive_map = cls.RECV_MAP if receive_map is None else receive_map
        pay_map = cls.PAY_MAP if pay_map is None else pay_map
        if not match or match[1].lower() not in receive_map or match[3].lower() not in pay_map:
            return None
        result = ExchangeCalculator(base_codes=base_codes).calculate(
            recv_code=receive_map[match[1].lower()], recv_amount_expr=match[2],
            pay_code=pay_map[match[3].lower()], pay_amount_expr=match[4], accounts=accounts,
        )
        return ParsedAcceptShortCommand(
            recv_code=result.recv_code, recv_amount_expr=match[2].strip(),
            pay_code=result.pay_code, pay_amount_expr=match[4].strip(), user_note=(match[5] or '').strip() or None,
            recv_amount=result.recv_amount, pay_amount=result.pay_amount,
            recv_prec=result.recv_precision, pay_prec=result.pay_precision, rate_str=result.rate_text,
        )

    async def parse_command(self, raw: str, *, chat_id: int, chat_name: str) -> ParsedAcceptShortCommand | None:
        if not self._COMMAND_RE.match(raw):
            return None
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)
        return self.parse_text(raw, accounts=accounts)

    async def execute_command(
        self,
        command: AcceptShortCommand,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
    ) -> None:
        parsed = await self.parse_command(
            command.text,
            chat_id=command.chat_id,
            chat_name=command.chat_name,
        )
        if not parsed:
            await replier.reply(self.help_text())
            return

        is_request_chat_origin = self.is_request_chat_origin(command.chat_id)
        recv_is_deposit = is_request_chat_origin
        pay_is_withdraw = is_request_chat_origin

        handled = await self._try_edit_request(
            command,
            parsed,
            recv_is_deposit,
            pay_is_withdraw,
            messenger=messenger,
            replier=replier,
        )
        if handled:
            return

        await self.create_request(
            CreateExchangeParams(
                chat_id=command.chat_id,
                chat_name=command.chat_name,
                source_message_id=command.message_id,
                recv_code=parsed.recv_code,
                recv_amount_expr=parsed.recv_amount_expr,
                pay_code=parsed.pay_code,
                pay_amount_expr=parsed.pay_amount_expr,
                creator_name=command.actor_name,
                note=parsed.user_note,
                reply_to_message_id=command.message_id,
            ),
            messenger=messenger,
            replier=replier,
        )

    async def _try_edit_request(
        self,
        command: AcceptShortCommand,
        parsed: ParsedAcceptShortCommand,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
    ) -> bool:
        reply = command.reply
        if reply is None or not reply.text:
            return False

        if reply.authored_by_bot:
            edit_req_id = extract_request_id(reply.text)
            if not edit_req_id:
                await replier.reply(
                    "Это сообщение бота не похоже на карточку заявки.\n"
                    "Чтобы изменить и пересчитать баланс, ответьте на сообщение БОТА с заявкой."
                )
                return True
            target_bot_msg_id = reply.message_id
        else:
            if extract_request_id(reply.text):
                await replier.reply(
                    "Похоже, вы ответили на пересланную/чужую карточку.\n"
                    "Ответьте на оригинальное сообщение БОТА с заявкой."
                )
                return True
            await replier.reply(
                "Чтобы изменить заявку, ответьте на сообщение БОТА с карточкой заявки."
            )
            return True

        creator_name = extract_created_by(reply.text) or command.actor_name
        await self.edit_exchange_request.execute_core(
            EditExchangeParams(
                chat_id=command.chat_id,
                chat_name=command.chat_name,
                edit_req_id=str(edit_req_id),
                target_bot_msg_id=target_bot_msg_id,
                old_card_text=reply.text,
                cmd_msg_id=command.message_id,
                recv_code=parsed.recv_code,
                pay_code=parsed.pay_code,
                recv_amount=parsed.recv_amount,
                pay_amount=parsed.pay_amount,
                recv_prec=parsed.recv_prec,
                pay_prec=parsed.pay_prec,
                rate_str=parsed.rate_str,
                user_note=parsed.user_note,
                creator_name=creator_name,
                recv_is_deposit=recv_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
            ),
            messenger=messenger,
            replier=replier,
        )
        return True
