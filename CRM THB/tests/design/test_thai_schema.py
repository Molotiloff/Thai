"""Executable design checks; application migrations are intentionally not run."""

from decimal import Decimal

import asyncpg
import pytest


async def operation(ledger, *, mode="ledger", key="test", reversal_of=None):
    return await ledger["db"].fetchval("""
        INSERT INTO financial_operations(
            kind,posting_mode,business_date,effective_at,created_by,
            idempotency_key,command_hash,reversal_of_id,reason
        ) VALUES ('test',$1,'2026-10-08',now(),$2,$3,$4,$5,$6) RETURNING id
    """, mode, ledger["user"], key, "a" * 64, reversal_of,
        "Correction" if reversal_of else None)


async def lines(ledger, op, *, credit_base="100", reverse=False):
    await ledger["db"].execute("""
        INSERT INTO journal_lines(operation_id,line_number,account_id,currency_code,
            side,native_amount,amount_usdt,valuation_basis) VALUES
            ($1,1,$2,'THB',$4,3500,100,'historical'),
            ($1,2,$3,'THB',$5,3500,$6,'historical')
    """, op, ledger["cash"], ledger["wallet"],
        "credit" if reverse else "debit", "debit" if reverse else "credit",
        Decimal(credit_base))


async def post(db, op):
    await db.execute("UPDATE financial_operations SET status='posted' WHERE id=$1", op)
    await db.execute("SET CONSTRAINTS ALL IMMEDIATE")


async def test_prototype_contains_expected_contexts(design_db):
    tables = set(await design_db.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
    names = {row[0] for row in tables}
    assert {"capital_interest_terms", "capital_withdrawal_allocations",
            "expense_statement_rows", "best_change_period_transfers",
            "business_client_bindings", "telegram_outbox"} <= names
    assert "loans" not in names


@pytest.mark.parametrize("domain,value", [
    ("quantity", "NaN"), ("usdt_amount", "Infinity"),
    ("positive_rate", "0"), ("positive_rate", "-1"), ("percentage", "101"),
])
async def test_domains_reject_invalid_financial_values(design_db, domain, value):
    with pytest.raises((asyncpg.CheckViolationError, asyncpg.NumericValueOutOfRangeError)):
        async with design_db.transaction():
            await design_db.fetchval(f"SELECT $1::numeric::{domain}", Decimal(value))


async def test_inverse_baht_rate_is_normalized(ledger):
    db = ledger["db"]
    rate = await db.fetchval("""
        INSERT INTO fx_rate_snapshots(currency_code,quoted_direction,quoted_rate,
            usdt_per_unit,effective_at,created_by)
        VALUES ('THB','units_per_usdt',35,round(1::numeric/35,18),now(),$1)
        RETURNING usdt_per_unit
    """, ledger["user"])
    assert rate == Decimal("0.028571428571428571")
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute("""
                INSERT INTO fx_rate_snapshots(currency_code,quoted_direction,quoted_rate,
                    usdt_per_unit,effective_at,created_by)
                VALUES ('THB','units_per_usdt',35,35,now(),$1)
            """, ledger["user"])


async def test_journal_currency_must_match_account(ledger):
    db = ledger["db"]
    op = await operation(ledger)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with db.transaction():
            await db.execute("""
                INSERT INTO journal_lines VALUES ($1,1,$2,'USDT','debit',1,1,'base',NULL)
            """, op, ledger["cash"])


async def test_draft_is_hidden_and_posted_wallet_is_loan_once(ledger):
    db = ledger["db"]
    op = await operation(ledger)
    await lines(ledger, op)
    assert await db.fetchval("SELECT balance FROM partner_loan_balances") == 0
    await post(db, op)
    assert await db.fetchval("SELECT balance FROM partner_loan_balances") == 3500
    assert await db.fetchval("SELECT carrying_usdt FROM partner_loan_balances") == 100
    await db.execute("UPDATE clients SET group_code='balance' WHERE id=$1", ledger["client"])
    assert await db.fetchval("SELECT count(*) FROM partner_loan_balances") == 0
    assert await db.fetchval("SELECT balance FROM posted_account_balances WHERE account_id=$1",
                             ledger["wallet"]) == 3500
    assert await db.fetchval("SELECT count(*) FROM journal_lines") == 2


@pytest.mark.parametrize("credit_base,add_lines", [("99", True), ("0", False)])
async def test_posting_requires_balanced_nonempty_journal(ledger, credit_base, add_lines):
    db = ledger["db"]
    with pytest.raises(asyncpg.CheckViolationError, match="not balanced"):
        async with db.transaction():
            op = await operation(ledger)
            if add_lines:
                await lines(ledger, op, credit_base=credit_base)
            await post(db, op)


@pytest.mark.parametrize("mutation", ["operation_update", "operation_delete",
                                     "journal_update", "journal_delete", "journal_append"])
async def test_posted_financial_history_is_immutable(ledger, mutation):
    db = ledger["db"]
    op = await operation(ledger)
    await lines(ledger, op)
    await post(db, op)
    commands = {
        "operation_update": "UPDATE financial_operations SET reason='changed' WHERE id=$1",
        "operation_delete": "DELETE FROM financial_operations WHERE id=$1",
        "journal_update": "UPDATE journal_lines SET amount_usdt=101 WHERE operation_id=$1",
        "journal_delete": "DELETE FROM journal_lines WHERE operation_id=$1",
        "journal_append": """INSERT INTO journal_lines
            SELECT operation_id,3,account_id,currency_code,side,native_amount,
                amount_usdt,valuation_basis,rate_snapshot_id
            FROM journal_lines WHERE operation_id=$1 AND line_number=1""",
    }
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(commands[mutation], op)


async def test_reversal_offsets_original_without_mutating_it(ledger):
    db = ledger["db"]
    original = await operation(ledger)
    await lines(ledger, original)
    await post(db, original)
    reverse = await operation(ledger, key="reverse", reversal_of=original)
    await lines(ledger, reverse, reverse=True)
    await post(db, reverse)
    assert await db.fetchval("SELECT balance FROM partner_loan_balances") == 0
    assert await db.fetchval("SELECT count(*) FROM journal_lines") == 4
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await operation(ledger, key="second-reverse", reversal_of=original)


async def test_idempotency_key_is_unique(ledger):
    await operation(ledger)
    with pytest.raises(asyncpg.UniqueViolationError):
        async with ledger["db"].transaction():
            await operation(ledger)


async def test_negative_partner_wallet_preserves_direction_of_debt(ledger):
    op = await operation(ledger)
    await lines(ledger, op, reverse=True)
    await post(ledger["db"], op)
    assert await ledger["db"].fetchval("SELECT balance FROM partner_loan_balances") == -3500


async def test_fifo_allocation_cannot_cross_capital_agreements(ledger):
    db = ledger["db"]
    agreements = [await db.fetchval("""
        INSERT INTO capital_agreements(investor_party_id,opened_on)
        VALUES ($1,'2026-10-01') RETURNING id
    """, ledger["party"]) for _ in range(2)]
    rate = await db.fetchval("""
        INSERT INTO fx_rate_snapshots(currency_code,quoted_direction,quoted_rate,
            usdt_per_unit,effective_at,created_by)
        VALUES ('USDT','usdt_per_unit',1,1,now(),$1) RETURNING id
    """, ledger["user"])
    deposit_op = await operation(ledger, key="deposit")
    withdrawal_op = await operation(ledger, key="withdrawal")
    lot = await db.fetchval("""
        INSERT INTO capital_contributions(agreement_id,operation_id,instrument_code,
            quantity,rate_snapshot_id,principal_usdt)
        VALUES ($1,$2,'USDT',100,$3,100) RETURNING id
    """, agreements[0], deposit_op, rate)
    withdrawal = await db.fetchval("""
        INSERT INTO capital_withdrawals(agreement_id,operation_id,payout_instrument_code,
            payout_quantity,principal_usdt)
        VALUES ($1,$2,'USDT',50,50) RETURNING id
    """, agreements[1], withdrawal_op)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with db.transaction():
            await db.execute("""
                INSERT INTO capital_withdrawal_allocations VALUES ($1,$2,$3,50,50)
            """, withdrawal, lot, agreements[1])


async def test_profit_closure_keeps_expenses_and_interest_separate(ledger):
    db = ledger["db"]
    term = await db.fetchval("""
        INSERT INTO profit_share_terms(valid_from,skyex_percent,pe_percent,created_by)
        VALUES ('2026-10-01',50,50,$1) RETURNING id
    """, ledger["user"])
    op = await operation(ledger, mode="memorandum")
    net = await db.fetchval("""
        INSERT INTO profit_period_closures(period,revision,operation_id,share_term_id,
            income_usdt,business_expenses_usdt,investor_interest_usdt)
        VALUES ('2026-10-01',1,$1,$2,1000,300,200) RETURNING net_profit_usdt
    """, op, term)
    assert net == 500


async def test_memorandum_can_post_without_cash_or_journal(ledger):
    db = ledger["db"]
    op = await operation(ledger, mode="memorandum")
    await post(db, op)
    assert await db.fetchval("SELECT count(*) FROM journal_lines") == 0
    assert await db.fetchval("SELECT balance FROM partner_loan_balances") == 0
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            invalid = await operation(ledger, mode="memorandum", key="invalid-memo")
            await lines(ledger, invalid)
            await post(db, invalid)


async def test_agreement_term_cannot_be_used_by_another_investor_agreement(ledger):
    db = ledger["db"]
    a = await db.fetchval("""
        INSERT INTO capital_agreements(investor_party_id,opened_on)
        VALUES ($1,'2026-10-01') RETURNING id
    """, ledger["party"])
    b = await db.fetchval("""
        INSERT INTO capital_agreements(investor_party_id,opened_on)
        VALUES ($1,'2026-10-01') RETURNING id
    """, ledger["party"])
    term = await db.fetchval("""
        INSERT INTO capital_interest_terms(agreement_id,monthly_interest_percent,valid_from,created_by)
        VALUES ($1,2,'2026-10-01',$2) RETURNING id
    """, a, ledger["user"])
    op = await operation(ledger)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with db.transaction():
            await db.execute("""
                INSERT INTO investor_interest_accruals(agreement_id,term_id,period,
                    basis_as_of,basis_usdt,calculated_usdt,operation_id)
                VALUES ($1,$2,'2026-10-01','2026-10-01',10000,200,$3)
            """, b, term, op)
    accrual = await db.fetchrow("""
        INSERT INTO investor_interest_accruals(agreement_id,term_id,period,basis_as_of,
            basis_usdt,calculated_usdt,adjustment_usdt,adjustment_reason,operation_id)
        VALUES ($1,$2,'2026-10-01','2026-10-01',10000,200,-50,'Manager decision',$3)
        RETURNING calculated_usdt,final_usdt
    """, a, term, op)
    assert tuple(accrual) == (Decimal("200"), Decimal("150"))


async def test_used_identity_cannot_be_deleted(ledger):
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with ledger["db"].transaction():
            await ledger["db"].execute("DELETE FROM parties WHERE id=$1", ledger["party"])


async def test_owner_profit_shares_must_total_one_hundred(ledger):
    with pytest.raises(asyncpg.CheckViolationError):
        async with ledger["db"].transaction():
            await ledger["db"].execute("""
                INSERT INTO profit_share_terms(valid_from,skyex_percent,pe_percent,created_by)
                VALUES ('2026-10-01',50,40,$1)
            """, ledger["user"])


async def test_expense_payer_and_self_obligation_constraints(ledger):
    db = ledger["db"]
    category = await db.fetchval(
        "INSERT INTO expense_categories(name) VALUES ('Rent') RETURNING id"
    )
    rate = await db.fetchval("""
        INSERT INTO fx_rate_snapshots(currency_code,quoted_direction,quoted_rate,
            usdt_per_unit,effective_at,created_by)
        VALUES ('THB','usdt_per_unit',0.03,0.03,now(),$1) RETURNING id
    """, ledger["user"])
    op = await operation(ledger)
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute("""
                INSERT INTO expenses(operation_id,category_id,currency_code,amount,
                    amount_usdt,rate_snapshot_id,payer_kind,payer_party_id,cash_instrument_code)
                VALUES ($1,$2,'THB',1000,30,$3,'party',$4,'THB_CASH')
            """, op, category, rate, ledger["party"])
    expense = await db.fetchval("""
        INSERT INTO expenses(operation_id,category_id,currency_code,amount,
            amount_usdt,rate_snapshot_id,payer_kind,payer_party_id)
        VALUES ($1,$2,'THB',1000,30,$3,'party',$4) RETURNING id
    """, op, category, rate, ledger["party"])
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute("""
                INSERT INTO expense_obligations(expense_id,currency_code,debtor_party_id,
                    creditor_party_id,amount,original_usdt)
                VALUES ($1,'THB',$2,$2,200,6)
            """, expense, ledger["party"])


async def test_statement_cannot_store_incorrect_closing_balance(ledger):
    db = ledger["db"]
    op = await operation(ledger, mode="memorandum")
    closure = await db.fetchval("""
        INSERT INTO expense_period_closures(period,revision,operation_id)
        VALUES ('2026-10-01',1,$1) RETURNING id
    """, op)
    creditor = await db.fetchval(
        "INSERT INTO parties(name,kind) VALUES ('PE','organization') RETURNING id"
    )
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute("""
                INSERT INTO expense_statement_rows VALUES
                    ($1,$2,$3,'THB',150,400,50,100,401)
            """, closure, ledger["party"], creditor)
    await db.execute("""
        INSERT INTO expense_statement_rows VALUES ($1,$2,$3,'THB',150,400,50,100,400)
    """, closure, ledger["party"], creditor)
    await post(db, op)


async def test_best_change_period_transfer_moves_existing_debt_without_cash(ledger):
    db = ledger["db"]
    second_client_party = await db.fetchval(
        "INSERT INTO parties(name,kind) VALUES ('BestChange chat','organization') RETURNING id"
    )
    second_client = await db.fetchval(
        "INSERT INTO clients(party_id,group_code) VALUES ($1,'balance') RETURNING id",
        second_client_party,
    )
    source = await db.fetchval("""
        INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,
            currency_code,instrument_code,party_id)
        VALUES ('buffer','liability','best_change_buffer','credit','USDT','USDT',$1)
        RETURNING id
    """, ledger["party"])
    target = await db.fetchval("""
        INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,
            currency_code,instrument_code,party_id)
        VALUES ('daniyar','liability','client_wallet','credit','USDT','USDT',$1)
        RETURNING id
    """, ledger["party"])
    await db.execute("""
        INSERT INTO client_wallet_accounts(client_id,instrument_code,ledger_account_id,kind)
        VALUES ($1,'USDT',$2,'profit_buffer'),($3,'USDT',$4,'client')
    """, second_client, source, ledger["client"], target)
    income = await db.fetchval("""
        INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code)
        VALUES ('income','income','trading_income','credit','USDT') RETURNING id
    """)
    accrual = await operation(ledger, key="accrue-buffer")
    await db.execute("""
        INSERT INTO journal_lines VALUES
            ($1,1,$2,'USDT','debit',1,1,'base',NULL),
            ($1,2,$3,'USDT','credit',1,1,'base',NULL)
    """, accrual, income, source)
    await post(db, accrual)
    transfer = await operation(ledger, key="close-buffer")
    await db.execute("""
        INSERT INTO best_change_period_transfers(period,revision,operation_id,
            source_wallet_account_id,target_wallet_account_id,amount_usdt)
        VALUES ('2026-10-01',1,$1,$2,$3,1)
    """, transfer, source, target)
    await db.execute("""
        INSERT INTO journal_lines VALUES
            ($1,1,$2,'USDT','debit',1,1,'base',NULL),
            ($1,2,$3,'USDT','credit',1,1,'base',NULL)
    """, transfer, source, target)
    await post(db, transfer)
    balances = dict(await db.fetch(
        "SELECT account_id,balance FROM posted_account_balances"
    ))
    assert balances[source] == 0
    assert balances[target] == 1
    assert balances[ledger["cash"]] == 0
    assert balances[income] == -1
