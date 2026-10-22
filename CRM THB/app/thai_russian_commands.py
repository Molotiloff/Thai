"""Familiar Russian bot interface over the isolated Thai accounting services."""

import re
from datetime import date
from decimal import DecimalException

from domain.finance.models import InvalidPosting, rounded
from services.cash_requests.constants import ACCOUNTING_CASH_SUFFIXES, ACCOUNTING_FX_CMD_MAP
from services.cash_requests.keyboard_port import NullCashKeyboardPresenter
from services.exchange.accept_short_service import AcceptShortService
from services.exchange.wallet_presenter import ExchangeWalletPresenter
from services.expression_calculator import CalcError, evaluate
from services.wallets.text_builder import WalletTextBuilder

INSTRUMENTS = {
    "бат": "THB_CASH", "thb": "THB_CASH", "нал": "THB_CASH",
    "безнал": "THB_BANK", "thb_bank": "THB_BANK",
    "usd": "USD_BL", "дол": "USD_BL", "доллар": "USD_BL",
    "usdw": "USD_WH", "долб": "USD_WH", "долбел": "USD_WH",
    "usdt": "USDT", "юсдт": "USDT", "eur": "EUR", "евро": "EUR",
    "eur500": "EUR500", "евро500": "EUR500", "rub": "RUB", "руб": "RUB",
    **{code.lower(): code for code in (
        "THB_CASH", "THB_BANK", "USD_BL", "USD_WH", "USDT", "EUR", "EUR500", "RUB"
    )},
}
LABELS = {"THB_CASH": "Бат наличный", "THB_BANK": "Бат безналичный",
          "USD_BL": "Доллар синий", "USD_WH": "Доллар белый", "USDT": "USDT",
          "EUR": "Евро", "EUR500": "Евро 500", "RUB": "Рубль"}
SUFFIXES = ACCOUNTING_CASH_SUFFIXES
RECEIVE = {"/"+name:code for name,code in AcceptShortService.ACCOUNTING_RECEIVE.items()}
PAY = AcceptShortService.ACCOUNTING_PAY
CASH = {"/" + prefix + suffix: (prefix == "деп", code)
        for prefix in ("деп", "выд") for suffix, code in SUFFIXES.items()}
FX_CASH = {'/'+name:definition for name,definition in ACCOUNTING_FX_CMD_MAP.items()}
CASH_HELP = "\n".join(
    f"{LABELS[code]}: /деп{suffix} · /выд{suffix}"
    for suffix, code in SUFFIXES.items()
)

HELP = f"""📖 Команды тайской CRM

/start — создать счёт клиента
/помоги — приветствие и условия
/кош — кошелёк USDT
/setwallet адрес — изменить кошелёк USDT (администратор)
/отпр [сумма] · /отпр тест [сумма] — ответом на адрес получателя; тест 1 USDT отдельно
/info — контакты
/whoami — Telegram ID пользователя и текущего чата
/акт [сумма или выражение] — служебный USDT-счёт чата заявок
/xe USD THB 1000 — кросс-курс XE, без изменения учёта
/calc (100+20)/3 · /100+20 — калькулятор
/сообщение название чата — ZIP/HTML-архив (админский чат)
/mgr [+ ID имя | - ID] — менеджеры (владелец/администратор)
/всем [группа] — рассылка с предпросмотром и подтверждением
/амл адрес — AML-проверка через существующую очередь
/кнопки · /скрыть — клавиатура

/кошелек — все счета клиента
/дай — ненулевые счета
/выписка [валюта] — движения со страницами; кнопки — XLSX
/клиенты — список клиентов
/бк [валюта] [+|-] — ненулевые балансы клиентов
/регистрация Имя — создать клиента и привязать чат
/привязать ID — привязать существующего клиента

Обмен в кошельке: принимаем /пбат, /пбезнал, /пд, /пб, /пт, /пе, /пп, /пр; отдаём обат, обезнал, од, об, от, ое, оп, ор.
Пример: /пбат 3500 от 100
Принимаем бат и отдаём другую валюту — продажа; принимаем валюту и отдаём бат — покупка. Остальные пары — конвертация. Наличный и безналичный бат — базовые счета.
Принимаемое списываем у клиента, выдаваемое зачисляем. Минус разрешён. Касса при таком обмене не меняется.

Внести в кассу: /депбат 3500
Выдать из кассы: /выдбат 3500
{CASH_HELP}
Нажмите «Готово к расчету», затем проведите фактическую сумму с номером заявки:
/бат 3500 Б-000001 — внесение; /бат -3500 Б-000001 — выдача.
Кассовый обмен: /пбатвт 3500 100 или /птвбат 100 3500.
После готовности подтвердите обе стороны: касса изменится одной операцией.

/перевод 1000 Имя клиента — перевод батов между клиентами
/перевод 100 USDT Имя клиента — перевод в указанной валюте
Выберите получателя и подтвердите. Ответ на чек: /перевод новая_сумма или /отмена
/USD 250 · /RUB -100 · /USDT (2+3*4) — ручное изменение кошелька, без кассы; комментарий можно дописать после суммы


/бест продажа 100 35 36 [бат|безнал]
/бест покупка 100 35 34 [бат|безнал]
/бест отчет [ММ.ГГГГ]
/бест баланс · /бест закрыть ММ.ГГГГ
/бест исправить ID покупка|продажа сумма курсРынка курсКлиента бат|безнал причина
/бест история ДД.ММ.ГГГГ покупка|продажа сумма курсРынка курсКлиента [бат|безнал]
/бест сторно IDоперации причина
/бест привязать IDбуферногоКлиента IDДанияра
/прибыль [ММ.ГГГГ]
/прибыль закрыть ММ.ГГГГ
/прибыль выплата скайекс|пе|площадка валюта сумма [курс]
/отмена IDзаявки причина · /сторно IDоперации причина
/закрыть причина — закрыть клиента с нулевыми остатками
/обнулить — подтвердить корректировки и закрытие клиента
/группа chat_id Баланс|Партнер — сменить группу клиента
/вход — открыть CRM через Telegram
/помощь — команды"""


WELCOME = """👋 Привет! Я бот команды SKYEX в Таиланде.

Я фиксирую детали сделки и веду её учёт.

📜 Условия работы:
❗ Фикс — это подтверждение курса сделки. После фикса курс закрепляется и не меняется.
В случае отмены или срыва фикса может применяться неустойка, если курс изменился и мы понесли убытки.

Перед отправкой USDT обязательно уточняйте актуальный кошелёк командой /кош или у менеджера.

/дай — актуальный баланс
/кош — актуальный кошелёк USDT
/info — возможности и контакты команды
/help — список команд
/кнопки — показать кнопки · /скрыть — скрыть"""

INFO = """ℹ️ SKYEX · Таиланд

Покупка, продажа и обмен валют. Наличный и безналичный бат, USDT, белый и синий доллар, евро, евро500 и рубль.
Условия, курс и время расчёта уточняйте у менеджера в этом чате.
Менеджеры: @skyex_support · @power_skyex
Бухгалтерия: @finance_skyex
Сайт: https://sky-ex.ru/"""



def instrument(value):
    try:
        return INSTRUMENTS[value.lower()]
    except KeyError:
        raise InvalidPosting("Неизвестная валюта. Доступны: бат, безнал, USDT, USD, USDW, евро, евро500, руб") from None


def amount(expression, code=None, *, signed=False):
    if len(expression) > 200:
        raise InvalidPosting("Слишком длинное выражение суммы")
    try:
        value = evaluate(expression)
    except (CalcError, DecimalException, RecursionError) as error:
        raise InvalidPosting("Некорректное выражение суммы") from error
    if code:
        value = rounded(value, 6 if code == "USDT" else 2)
    if not value.is_finite() or value == 0 or (value < 0 and not signed):
        raise InvalidPosting("Сумма должна быть ненулевой" if signed else "Сумма должна быть положительной")
    return value


def month(value):
    try:
        result = date(int(value[3:]), int(value[:2]), 1) if re.fullmatch(r"\d{2}\.\d{4}", value) else date.fromisoformat(value)
    except ValueError:
        raise InvalidPosting("Укажите месяц в формате ММ.ГГГГ") from None
    if result.day != 1:
        raise InvalidPosting("Укажите месяц в формате ММ.ГГГГ")
    return result


class RussianTelegramCommands:
    def __init__(self, app, config):
        self._app, self._config = app, config

    async def execute_customer(self, *, command, args, key, telegram_user_id, chat_id, chat_name):
        if command in ("/start", "/дай"):
            name = chat_name or await self._app.client_queries.chat_name(chat_id) or f"Клиент {chat_id}"
            client_id = await self._app.client_bootstrap.ensure_telegram_client_wallet(
                key=key, telegram_user_id=telegram_user_id, chat_id=chat_id,
                chat_name=name,
            )
            if command == "/start":
                return WELCOME
            if len(args) > 1:
                raise InvalidPosting("/дай [валюта]")
            code = instrument(args[0]) if args else None
            rows = await self._wallet_rows(client_id)
            if code:
                return WalletTextBuilder.wallet_text(chat_name=name, rows=[r for r in rows if r['currency_code']==code])
            return ExchangeWalletPresenter.summary(name, rows)
        if command == "/помоги":
            return WELCOME
        if command in ("/help", "/помощь", "/команды"):
            return HELP
        if command == "/info":
            return INFO
        if command == "/кош" or (command == "/usdt" and not args):
            return await self._app.payment_wallet.current_accounting(self._config.usdt_wallet) or "Актуальный адрес USDT уточните у менеджера в этом чате."
        if command == "/кнопки":
            return "Клавиатура включена. Выберите команду ниже:"
        if command == "/скрыть":
            return "Клавиатура скрыта. Чтобы вернуть — /кнопки."
        return None

    async def execute(self, *, command, args, text, chat_id, common, chat_name=None, telegram_user_id=None, actor_name=None):
        response = await self._workflow(command, args, chat_id, common)
        if response is not None:
            return response
        if command in ('/setwallet','/setкош'):
            if len(args)!=1:
                raise InvalidPosting('/setwallet адресUSDT')
            await self._app.payment_wallet.set_accounting(key=common['key'],actor_id=common['actor_id'],address=args[0])
            return '✅ USDT-кошелёк обновлён.'
        if command == "/вход":
            origin = self._config.frontend_origin
            return f"Войти через Telegram: {origin}/login" if origin else "Адрес CRM ещё не настроен"
        if command == "/клиенты":
            return '\n\n'.join(await self._app.client_directory.build_clients_chunks(group=' '.join(args) or None))
        if command == "/бк":
            from services.client_balances import MINUS_CHARS, PLUS_CHARS
            sign = args[1] if len(args)==2 else None
            if len(args)>2 or (sign is not None and sign not in MINUS_CHARS+PLUS_CHARS):
                raise InvalidPosting('/бк [валюта] [+|-]')
            return '\n\n'.join(await self._app.client_balances.build_report(
                code_filter=instrument(args[0]) if args else None,sign_filter=sign))
        if command == "/бест":
            return await self._best(args, common)
        if command == "/прибыль":
            return await self._profit(args, common)
        if command[1:] in INSTRUMENTS and len(args)==2 and re.fullmatch(r'Б-\d+', args[1]):
            request = await self._app.trading_queries.detail(self._app.cash_requests.deal_id(args[1]))
            allowed_chat = self._config.operations_chat_id or request['attributes'].get('source_chat_id')
            if chat_id != allowed_chat:
                raise InvalidPosting('Кассовый расчёт проводится в рабочем чате заявки')
            code = instrument(command[1:])
            signed = amount(args[0], code, signed=True)
            result = await self._app.cash_requests.settle_request(
                **common, request_id=args[1], instrument=code, signed_quantity=signed)
            if isinstance(result,dict):
                return (f"Заявка {args[1]} проведена: операция {result['operation_id']}" if result['operation_id']
                    else f"Заявка {args[1]}: сторона подтверждена; ожидается вторая сторона, касса не изменена")
            return f"Заявка {args[1]} проведена: операция {result.id}"
        relevant = command in RECEIVE or command in CASH or command in FX_CASH or command[1:] in INSTRUMENTS or command in (
            "/кошелек", "/кошелёк", "/добавь", "/удали",
        )
        if not relevant:
            return None
        client_id = await self._app.trading_queries.client_by_chat(chat_id)
        if client_id is None and chat_name:
            client_id = await self._app.client_bootstrap.ensure_telegram_client_wallet(
                key=common["key"] + ":client", telegram_user_id=telegram_user_id,
                chat_name=chat_name, chat_id=chat_id,
            )
        if client_id is None:
            raise InvalidPosting("Сначала /регистрация Имя или /привязать ID")
        if command in ("/кошелек", "/кошелёк"):
            name = chat_name or await self._app.client_queries.chat_name(chat_id) or f"Клиент {chat_id}"
            return WalletTextBuilder.wallet_text(chat_name=name, rows=await self._wallet_rows(client_id))
        if command in ("/добавь", "/удали"):
            return "В тайской CRM восемь постоянных счетов; они создаются автоматически. Посмотреть: /кошелек"
        if command in RECEIVE:
            return await self._exchange(text, client_id, common, chat_id=chat_id,
                chat_name=chat_name, actor_name=actor_name or str(telegram_user_id))
        if command in CASH or command in FX_CASH:
            parsed = self._app.cash_requests.parse_command(text)
            if parsed is None:
                if command in FX_CASH:
                    raise InvalidPosting(self._app.cash_requests.help_text())
                raise InvalidPosting(f"{command} сумма [контакт] [контакт] [! комментарий]")
            request = await self._app.cash_requests.create_request(
                key=common['key']+':cash-request', actor_id=common['actor_id'], client_id=client_id,
                parsed=parsed, client_name=chat_name or await self._app.client_queries.chat_name(chat_id),
                creator_name=actor_name or str(telegram_user_id), source_chat_id=chat_id)
            plan = self._app.cash_requests.card_plan(request, NullCashKeyboardPresenter())
            return plan.request_text if chat_id==self._config.operations_chat_id else plan.client_text
        if not args:
            raise InvalidPosting(f"{command} сумма [комментарий]")
        code = instrument(command[1:])
        result = await self._app.wallet_interactions.accounting_adjust(
            **common, client_id=client_id, instrument=code, quantity=amount(args[0], code, signed=True),
             comment=" ".join(args[1:]),
        )
        row = next(row for row in await self._app.client_queries.wallets(client_id) if row['instrument_code']==code)
        return self._app.wallet_interactions.accounting_response(
            {'quantity':amount(args[0],code,signed=True),'instrument':code},row['balance'])

    async def _workflow(self, command, args, chat_id, common):
        """Document lifecycle commands use the same application services as CRM."""
        if command == '/регистрация':
            if not args:
                raise InvalidPosting('/регистрация Имя')
            created = await self._app.clients.create_client(key=common['key'],actor_id=common['actor_id'],
                name=' '.join(args),chat_id=chat_id)
            return f"Клиент {created['client_id']} зарегистрирован; восемь кошельков, THB/USDT"
        if command == '/привязать':
            if len(args)!=1:
                raise InvalidPosting('/привязать IDклиента')
            await self._app.clients.bind_chat(key=common['key'],actor_id=common['actor_id'],client_id=int(args[0]),chat_id=chat_id)
            return 'Чат привязан к клиенту'
        if command in ('/готово','/провести','/отмена'):
            if not args:
                raise InvalidPosting(f'{command} IDзаявки' + (' причина' if command=='/отмена' else ''))
            deal_id = self._app.cash_requests.deal_id(args[0]) if args[0].startswith('Б-') else int(args[0])
            deal = await self._app.trading_queries.detail(deal_id)
            cash = deal['kind'] in ('deposit','withdrawal','cash_exchange')
            if command=='/готово':
                if len(args)!=1:
                    raise InvalidPosting('/готово IDзаявки')
                if cash:
                    await self._app.cash_requests.change_request_status(key=common['key'],actor_id=common['actor_id'],request_id=f'Б-{deal_id:06d}',action='ready')
                    return 'Заявка готова к расчёту; деньги ещё не изменены'
                await self._app.trading.ready(key=common['key'],actor_id=common['actor_id'],deal_id=deal_id)
                return 'Заявка готова к проведению; деньги ещё не изменены'
            if command=='/провести':
                if len(args)!=1:
                    raise InvalidPosting('/провести IDзаявки')
                result = await self._app.trading.post(**common,deal_id=deal_id)
                return f'Проведено: операция {result.id}, результат в USDT'
            reason=' '.join(args[1:])
            if deal['kind']=='fx_trade':
                context=await self._app.trading_queries.exchange_context(deal_id)
                await self._app.exchange_requests.cancel_accounting(deal_id=deal_id,expected_deal_id=context['active']['id'],common=common,reason=reason)
            elif cash and deal['operation_id'] is None:
                await self._app.cash_requests.change_request_status(key=common['key'],actor_id=common['actor_id'],request_id=f'Б-{deal_id:06d}',action='cancel',reason=reason)
            elif deal['operation_id'] is None:
                await self._app.trading.cancel_draft(key=common['key'],actor_id=common['actor_id'],deal_id=deal_id,reason=reason)
            else:
                await self._app.trading.reverse(**common,deal_id=deal_id,reason=reason)
            return 'Заявка отменена; история сохранена'
        if command=='/сторно':
            if len(args)<2:
                raise InvalidPosting('/сторно IDоперации причина')
            operation_id=int(args[0])
            deal_id=await self._app.pool.fetchval("SELECT id FROM deals WHERE operation_id=$1 AND kind IN ('deposit','withdrawal','cash_exchange','fx_trade')",operation_id)
            if deal_id is not None:
                result=await self._app.trading.reverse(**common,deal_id=deal_id,reason=' '.join(args[1:]))
            else:
                result=await self._app.reversals.reverse(**common,original_id=operation_id,reason=' '.join(args[1:]))
            return f'Сторно {result.id}; история сохранена'
        if command in ('/закрыть','/выписка'):
            client_id=await self._app.trading_queries.client_by_chat(chat_id)
            if client_id is None:
                raise InvalidPosting('Сначала /регистрация Имя или /привязать ID')
            if command=='/закрыть':
                await self._app.clients.close(key=common['key'],actor_id=common['actor_id'],client_id=client_id,reason=' '.join(args))
                return 'Клиент закрыт; история сохранена'
            if len(args)>1:
                raise InvalidPosting('/выписка [валюта]')
            return (await self._app.wallet_interactions.statement_page(client_id=client_id,instrument=instrument(args[0]) if args else 'THB_CASH'))['text']
        return None

    async def _wallet_rows(self, client_id):
        return [{**row, 'currency_code': row['instrument_code']} for row in await self._app.client_queries.wallets(client_id)]

    async def _exchange(self, text, client_id, common, *, chat_id, chat_name, actor_name):
        context = await self._app.exchange_requests.create_accounting(text=text,client_id=client_id,
            common=common,chat_id=chat_id,chat_name=chat_name or await self._app.client_queries.chat_name(chat_id),
            actor_name=actor_name)
        texts = await self._app.exchange_requests.accounting_texts(context)
        return texts.request_text if chat_id==self._config.operations_chat_id else texts.client_text

    async def _best(self, args, common):
        from handlers.best_change import format_best_change_result
        from services.best_change.reporting import format_month_report
        if not args or args[0].lower() in ('отчет','отчёт','баланс'):
            if len(args)>2:
                raise InvalidPosting('/бест отчет [ММ.ГГГГ]')
            period = month(args[1]) if len(args)==2 else common['business_date'].replace(day=1)
            return format_month_report(await self._app.profit_queries.best_change_report(period))
        if args[0].lower()=='привязать' and len(args)==3:
            await self._app.best_change.configure(**common,buffer_client_id=int(args[1]),daniyar_client_id=int(args[2]))
            return 'Привязки BestChange и Баланс Данияр сохранены'
        if args[0].lower()=='закрыть' and len(args)==2:
            await self._app.profit.close(key=common['key'],actor_id=common['actor_id'],period=month(args[1]),effective_at=common['effective_at'])
            return format_month_report(await self._app.profit_queries.best_change_report(month(args[1])))
        if args[0].lower()=='сторно' and len(args)>=3:
            op = await self._app.profit.reverse(**common,operation_id=int(args[1]),reason=' '.join(args[2:]))
            return f'Сторно BestChange: операция {op.id}'
        operations = {'продажа':'sale','продать':'sale','покупка':'purchase','купить':'purchase'}
        if args[0].lower()=='исправить':
            if len(args)<8 or args[2].lower() not in operations:
                raise InvalidPosting('/бест исправить ID покупка|продажа суммаUSDT курсРынка курсКлиента бат|безнал причина')
            context = await self._app.profit_queries.best_change_context(int(args[1]))
            await self._app.best_change.correct(**common,deal_id=int(args[1]),expected_deal_id=context['active']['deal_id'],
                reason=' '.join(args[7:]),replacement={'operation':operations[args[2].lower()],'quantity':amount(args[3],'USDT'),
                    'market_rate':args[4].replace(',','.'),'client_rate':args[5].replace(',','.'),'thb_instrument':instrument(args[6])})
            return format_best_change_result(await self._app.profit_queries.best_change_context(int(args[1])))
        if args[0].lower()=='история':
            if len(args) not in (6,7):
                raise InvalidPosting('/бест история ДД.ММ.ГГГГ покупка|продажа суммаUSDT курсРынка курсКлиента [бат|безнал]')
            from datetime import datetime
            from zoneinfo import ZoneInfo
            day = datetime.strptime(args[1],'%d.%m.%Y').date()
            common = {**common,'business_date':day,'effective_at':datetime.combine(day,datetime.min.time(),ZoneInfo('Asia/Bangkok'))}
            args = args[2:]
        if args[0].lower() not in operations or len(args) not in (4,5):
            raise InvalidPosting('/бест покупка|продажа суммаUSDT курсРынка курсКлиента [бат|безнал]')
        posted = await self._app.best_change.record(**common,operation=operations[args[0].lower()],quantity=amount(args[1],'USDT'),
            market_rate=args[2].replace(',','.'),client_rate=args[3].replace(',','.'),thb_instrument=instrument(args[4]) if len(args)==5 else 'THB_CASH',
            mode='cash',external_reference=common['key'])
        deal = await self._app.pool.fetchval('SELECT id FROM deals WHERE operation_id=$1',posted.id)
        return format_best_change_result(await self._app.profit_queries.best_change_context(deal))

    async def _profit(self, args, common):
        if args and args[0].lower() == "закрыть":
            if len(args) != 2:
                raise InvalidPosting("/прибыль закрыть ММ.ГГГГ")
            result = await self._app.profit.close(key=common["key"], actor_id=common["actor_id"],
                                                period=month(args[1]), effective_at=common["effective_at"])
            return f"Период закрыт: операция {result.id}; доля Данияра перенесена на Баланс Данияр"
        if args and args[0].lower() == "выплата":
            recipients = {"скайекс": "skyex", "скай": "skyex", "skyex": "skyex", "пе": "pe", "площадка": "platform"}
            if len(args) not in (4, 5) or args[1].lower() not in recipients:
                raise InvalidPosting("/прибыль выплата скайекс|пе|площадка валюта сумма [курс]")
            code = instrument(args[2])
            result = await self._app.profit.pay(**common, recipient=recipients[args[1].lower()],
                instrument=code, quantity=amount(args[3], code), quoted_rate=amount(args[4]) if len(args) == 5 else None)
            return f"Выплата проведена: операция {result.id}"
        if len(args) > 1:
            raise InvalidPosting("/прибыль [ММ.ГГГГ]")
        period = month(args[0]) if args else common["business_date"].replace(day=1)
        report = await self._app.profit_queries.report(period)
        return f"{period:%m.%Y} · USDT\nДоход: {report['income_usdt']}\nРасходы: {report['business_expenses_usdt']}\nПроценты: {report['investor_interest_usdt']}\nЧистая прибыль: {report['net_profit_usdt']}"
