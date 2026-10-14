from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from config import Config
from db_asyncpg.repositories.act_counter import ActCounterRepo
from db_asyncpg.repositories.message_archive import MessageArchiveRepo
from db_asyncpg.thai_capital.queries import CapitalQueries
from db_asyncpg.thai_capital.uow import AsyncpgCapitalUnitOfWork
from db_asyncpg.thai_clients.queries import ClientQueries
from db_asyncpg.thai_clients.uow import AsyncpgClientUnitOfWork
from db_asyncpg.thai_expenses.queries import ExpenseQueries
from db_asyncpg.thai_expenses.uow import AsyncpgExpenseUnitOfWork
from db_asyncpg.thai_finance.queries import FinanceQueries, OutboxDeliveryRepository
from db_asyncpg.thai_finance.uow import AsyncpgFinanceUnitOfWork
from db_asyncpg.thai_profit.queries import ProfitQueries
from db_asyncpg.thai_profit.uow import AsyncpgProfitUnitOfWork
from db_asyncpg.thai_trading.queries import TradingQueries
from db_asyncpg.thai_trading.uow import AsyncpgTradingUnitOfWork
from services.accounting.cash_settlement_service import CashSettlementService
from services.accounting.profit_service import ProfitService
from services.act_counter import ActCounterService
from services.admin_client.client_bootstrap_service import ClientBootstrapService
from services.admin_client.client_directory_service import (
    ClientDirectoryService as ClientDirectoryPresenter,
)
from services.admin_client.client_group_service import ClientGroupService
from services.admin_client.manager_admin_service import ManagerAdminService
from services.admin_client.usdt_wallet_service import UsdtWalletService
from services.best_change.service import BestChangeLedgerService
from services.capital.service import CapitalService
from services.cash_requests.constants import ACCOUNTING_CMD_MAP, ACCOUNTING_FX_CMD_MAP
from services.cash_requests.request_service import CashRequestService
from services.client_balances import (
    ClientBalancesFilterService,
    ClientBalancesQueryService,
    ClientBalancesReportBuilder,
    DailyBalancesReportService,
)
from services.clients.adjustments import WalletAdjustmentService
from services.clients.conversions import WalletConversionService
from services.clients.directory import ClientDirectoryService
from services.clients.transfers import ClientWalletTransferService
from services.clients.wallets import WalletCashService
from services.crm.client_transfer_service import ClientTransferService
from services.crm.client_transfer_workflow import ClientTransferWorkflow
from services.exchange.accept_short_service import AcceptShortService
from services.exchange.source_link_service import ExchangeSourceLinkService
from services.exchange.transaction_service import TradingService
from services.expenses.service import ExpenseService
from services.finance.outbox import DeliveryPort, OutboxDispatcher
from services.finance.ports import FinanceUowFactory
from services.finance.posting import (
    CashTransferService,
    OperationPostingService,
    ProjectionRebuildService,
    ReversalService,
)
from services.loans import LoanQueryService
from services.payment_watch.service import PaymentWatchService
from services.wallets.interaction_service import WalletInteractionService
from services.wallets.mutation_service import CurrencyMutationService
from services.wallets.query_service import WalletQueryService
from services.wallets.undo_service import WalletUndoService


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    pool: asyncpg.Pool
    uow_factory: FinanceUowFactory
    posting: OperationPostingService
    cash_transfers: CashTransferService
    reversals: ReversalService
    projections: ProjectionRebuildService
    queries: FinanceQueries
    capital: CapitalService
    capital_queries: CapitalQueries
    clients: ClientDirectoryService
    client_queries: ClientQueries
    wallets: WalletCashService
    loans: LoanQueryService
    trading: TradingService
    trading_queries: TradingQueries
    conversions: WalletConversionService
    expenses: ExpenseService
    expense_queries: ExpenseQueries
    best_change: BestChangeLedgerService
    profit: ProfitService
    profit_queries: ProfitQueries
    wallet_transfers: ClientWalletTransferService
    wallet_adjustments: WalletAdjustmentService
    cash_requests: CashRequestService
    client_bootstrap: ClientBootstrapService
    exchange_requests: AcceptShortService
    client_transfers: ClientTransferWorkflow
    wallet_interactions: WalletInteractionService
    client_directory: ClientDirectoryPresenter
    client_balances: DailyBalancesReportService
    client_groups: ClientGroupService
    payment_watches: PaymentWatchService
    payment_wallet: UsdtWalletService
    message_archive: MessageArchiveRepo
    act_counter: ActCounterService
    manager_admin: ManagerAdminService

    config: Config | None = None

    @classmethod
    def build(cls, pool: asyncpg.Pool, *, config: Config | None = None) -> ApplicationContainer:
        def uow() -> AsyncpgFinanceUnitOfWork:
            return AsyncpgFinanceUnitOfWork(pool)

        posting = OperationPostingService(uow)

        def capital_uow() -> AsyncpgCapitalUnitOfWork:
            return AsyncpgCapitalUnitOfWork(pool)

        capital_queries = CapitalQueries(pool)
        capital = CapitalService(
            OperationPostingService(capital_uow), ReversalService(capital_uow), capital_queries
        )

        def client_uow() -> AsyncpgClientUnitOfWork:
            return AsyncpgClientUnitOfWork(pool)

        client_queries = ClientQueries(pool)
        directory = ClientDirectoryService(client_uow)

        def trading_uow():
            return AsyncpgTradingUnitOfWork(pool)

        wallets = WalletCashService(OperationPostingService(trading_uow), client_queries)
        trading_queries = TradingQueries(pool)
        trading = TradingService(
            OperationPostingService(trading_uow),
            ReversalService(trading_uow),
            trading_queries,
            trading_uow,
        )
        conversions = WalletConversionService(
            OperationPostingService(client_uow), client_queries, trading_queries
        )

        def expense_uow():
            return AsyncpgExpenseUnitOfWork(pool)

        expense_queries = ExpenseQueries(pool)
        expenses = ExpenseService(
            OperationPostingService(expense_uow), ReversalService(expense_uow), expense_queries
        )

        def profit_uow():
            return AsyncpgProfitUnitOfWork(pool)

        profit_queries = ProfitQueries(pool)
        profit_posting = OperationPostingService(profit_uow)
        profit = ProfitService(profit_posting, ReversalService(profit_uow), profit_queries)
        best_change = BestChangeLedgerService(profit_posting,profit_queries,trading_queries,uow_factory=profit_uow,profit=profit)
        wallet_transfers = ClientWalletTransferService(OperationPostingService(client_uow),client_queries)
        client_transfers = ClientTransferWorkflow(ClientTransferService(uow_factory=client_uow,queries=client_queries,
            transfers=wallet_transfers,reversals=ReversalService(client_uow),trading_queries=trading_queries))
        adjustments = WalletAdjustmentService(OperationPostingService(client_uow),client_queries,client_uow)
        interactions = WalletInteractionService(query_service=WalletQueryService(accounting_queries=client_queries),
            mutation_service=CurrencyMutationService(accounting_adjustments=adjustments),
            undo_service=WalletUndoService(accounting_queries=client_queries,reversals=ReversalService(client_uow),uow_factory=client_uow))
        return cls(
            pool,
            uow,
            posting,
            CashTransferService(posting),
            ReversalService(uow),
            ProjectionRebuildService(uow),
            FinanceQueries(pool),
            capital,
            capital_queries,
            directory,
            client_queries,
            wallets,
            LoanQueryService(client_queries),
            trading,
            trading_queries,
            conversions,
            expenses,
            expense_queries,
            best_change,
            profit,
            profit_queries,
            wallet_transfers,
            adjustments,
            CashRequestService(cmd_map=ACCOUNTING_CMD_MAP, fx_cmd_map=ACCOUNTING_FX_CMD_MAP, admin_chat_ids=set(), admin_user_ids=set(),
                ledger_uow_factory=trading_uow, wallet_queries=client_queries, trade_queries=trading_queries, wallets=wallets,
                settlements=CashSettlementService(ledger_uow_factory=trading_uow,trading=trading,queries=trading_queries)),
            ClientBootstrapService(uow_factory=client_uow),
            AcceptShortService(accounting_trading=trading,wallet_queries=client_queries,trading_queries=trading_queries,
                source_links=ExchangeSourceLinkService(accounting_repository=trading_queries)),
            client_transfers,
            interactions,
            ClientDirectoryPresenter(client_queries),
            DailyBalancesReportService(query_service=ClientBalancesQueryService(client_queries),
                filter_service=ClientBalancesFilterService(aliases={}),report_builder=ClientBalancesReportBuilder()),
            ClientGroupService(directory=directory,queries=client_queries,trading_queries=trading_queries),
            PaymentWatchService(accounting_uow_factory=trading_uow,accounting_queries=trading_queries,
                wallet_queries=client_queries,wallets=wallets,trading=trading),
            UsdtWalletService(uow_factory=trading_uow,queries=trading_queries),
            MessageArchiveRepo(pool,client_lookup=client_queries),
            ActCounterService(account=ActCounterRepo(pool),uow_factory=client_uow),
            ManagerAdminService(client_queries,uow_factory=client_uow),
            config,
        )

    def outbox_dispatcher(self, delivery: DeliveryPort) -> OutboxDispatcher:
        return OutboxDispatcher(OutboxDeliveryRepository(self.pool), delivery)

    async def initialize_business_chats(self):
        if self.config is None or not self.config.best_change_chat_id or not self.config.daniyar_chat_id:
            return None
        actor=await self.pool.fetchval("""
            SELECT u.id FROM users u WHERE u.is_active AND EXISTS(
                SELECT 1 FROM user_roles r JOIN role_permissions p ON p.role=r.role
                WHERE r.user_id=u.id AND p.permission='terms.manage')
            ORDER BY (u.telegram_user_id=ANY($1::bigint[])) DESC,u.id LIMIT 1
        """,list(self.config.admin_ids))
        if actor is None:
            raise RuntimeError('Для привязки чатов из env нужен активный администратор')
        return await self.best_change.configure_from_chats(actor_id=actor,
            best_change_chat_id=self.config.best_change_chat_id,daniyar_chat_id=self.config.daniyar_chat_id)
