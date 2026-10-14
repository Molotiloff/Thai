import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.container import ApplicationContainer
from app.thai_telegram import ThaiTelegramDelivery
from db_asyncpg.thai_finance.queries import OutboxDeliveryRepository
from domain.finance.models import InvalidPosting
from tests.thai.test_expenses import pay, record
from tests.thai.test_posting import fund
from tests.thai.test_profit import close, deal


def configured_app(finance,**changes):
    config=replace(finance['config'],best_change_chat_id=-101,daniyar_chat_id=-102,**changes)
    return ApplicationContainer.build(finance['pool'],config=config)


async def test_env_business_chats_are_registered_once_and_reuse_existing_wallets(finance):
    existing=await finance['app'].clients.create_client(key='existing',actor_id=finance['actor'],
        name='Существующий чат Данияра',chat_id=-102)
    app=configured_app(finance)
    first,second=await asyncio.gather(app.initialize_business_chats(),app.initialize_business_chats())
    assert first==second
    assert first['daniyar_balance']==existing['client_id']
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients')==2
    assert await finance['pool'].fetchval("SELECT count(*) FROM financial_operations WHERE kind='profit_configure'")==1
    assert await finance['pool'].fetchval('SELECT p.name FROM clients c JOIN parties p ON p.id=c.party_id WHERE c.id=$1',existing['client_id'])=='Существующий чат Данияра'
    assert len(await app.client_queries.wallets(first['daniyar_balance']))==8
    assert len(await app.client_queries.wallets(first['best_change_buffer']))==8
    bindings=await app.profit_queries.bindings()
    assert bindings['daniyar_balance']['client_id']==first['daniyar_balance']
    bot=SimpleNamespace(send_message=AsyncMock(),edit_message_text=AsyncMock())
    event=await OutboxDeliveryRepository(finance['pool']).claim()
    await ThaiTelegramDelivery(app,bot,-999).deliver(event)
    bot.send_message.assert_not_awaited()


async def test_configured_daniyar_wallet_receives_bestchange_and_pays_expense_share(finance):
    app=configured_app(finance)
    clients=await app.initialize_business_chats()
    await fund(finance)
    await fund(finance,key='fund-usdt',instrument='USDT',quantity=Decimal(100))
    dany=clients['daniyar_balance']
    await record(finance,payer='business')
    assert (next(row for row in await app.client_queries.wallets(dany) if row['instrument_code']=='THB_CASH'))['balance']==0
    settlement=await pay(finance,creditor='business',method='wallet',amount='200',instrument='THB_CASH',quoted_rate='35')
    assert (next(row for row in await app.client_queries.wallets(dany) if row['instrument_code']=='THB_CASH'))['balance']==-200
    assert await pay(finance,creditor='business',method='wallet',amount='200',instrument='THB_CASH',quoted_rate='35')==settlement
    await deal(finance)
    await close(finance)
    usdt=next(row for row in await app.client_queries.wallets(dany) if row['instrument_code']=='USDT')
    assert usdt['balance']>0


async def test_env_change_cannot_reassign_existing_business_accounts(finance):
    app=configured_app(finance)
    before=await app.initialize_business_chats()
    with pytest.raises(InvalidPosting,match='не переназначаются'):
        await app.best_change.configure_from_chats(actor_id=finance['actor'],best_change_chat_id=-201,daniyar_chat_id=-202)
    assert await finance['pool'].fetchval('SELECT count(*) FROM clients')==2
    assert await app.initialize_business_chats()==before
