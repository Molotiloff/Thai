-- GENERATED Thai schema snapshot. Source: thai_migrations/sql/*.sql.
SET search_path TO public;
-- Immutable initial Thai schema, based on the reviewed E1 design.
-- Alembic selects public and verifies an empty or matching database first.
DO $$
BEGIN
    IF current_schema()<>'public' OR EXISTS (
        SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version_thai'
    ) THEN
        RAISE EXCEPTION 'Initial Thai migration requires an empty public schema';
    END IF;
END $$;
CREATE TABLE crm_schema_identity (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    family TEXT NOT NULL CHECK (family = 'thai'),
    accounting_currency TEXT NOT NULL CHECK (accounting_currency = 'USDT'),
    operational_currency TEXT NOT NULL CHECK (operational_currency = 'THB'),
    business_timezone TEXT NOT NULL CHECK (business_timezone = 'Asia/Bangkok'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO crm_schema_identity(singleton,family,accounting_currency,operational_currency,business_timezone)
VALUES (TRUE,'thai','USDT','THB','Asia/Bangkok');
CREATE DOMAIN quantity AS NUMERIC(38,8)
    CHECK (VALUE NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric));
CREATE DOMAIN usdt_amount AS NUMERIC(38,8)
    CHECK (VALUE NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric));
CREATE DOMAIN positive_rate AS NUMERIC(38,18)
    CHECK (VALUE > 0 AND VALUE NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric));
CREATE DOMAIN percentage AS NUMERIC(9,6)
    CHECK (VALUE BETWEEN 0 AND 100);

CREATE TABLE currencies (
    code TEXT PRIMARY KEY CHECK (code ~ '^[A-Z0-9]{2,12}$'),
    name TEXT NOT NULL CHECK (btrim(name) <> ''),
    precision SMALLINT NOT NULL CHECK (precision BETWEEN 0 AND 8)
);
CREATE TABLE instruments (
    code TEXT PRIMARY KEY CHECK (code ~ '^[A-Z0-9_]{2,20}$'),
    currency_code TEXT NOT NULL REFERENCES currencies(code),
    name TEXT NOT NULL,
    storage_kind TEXT NOT NULL CHECK (storage_kind IN ('cash','bank','crypto')),
    precision SMALLINT NOT NULL CHECK (precision BETWEEN 0 AND 8),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (code, currency_code)
);
CREATE TABLE instrument_aliases (
    alias TEXT PRIMARY KEY CHECK (alias = lower(btrim(alias)) AND alias <> ''),
    instrument_code TEXT NOT NULL REFERENCES instruments(code)
);
CREATE TABLE parties (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL CHECK (btrim(name) <> ''),
    kind TEXT NOT NULL CHECK (kind IN ('person','organization','business')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE party_roles (
    party_id BIGINT NOT NULL REFERENCES parties(id),
    role TEXT NOT NULL CHECK (role IN ('owner','investor','client')),
    PRIMARY KEY (party_id, role)
);
CREATE TABLE business_participants (
    code TEXT PRIMARY KEY CHECK (code IN ('business','skyex','pe','daniyar')),
    party_id BIGINT NOT NULL UNIQUE REFERENCES parties(id)
);
CREATE TABLE users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_user_id BIGINT UNIQUE,
    display_name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE user_roles (
    user_id BIGINT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL CHECK (role IN ('cashier','manager','accountant','owner','admin')),
    PRIMARY KEY (user_id, role)
);
CREATE TABLE client_groups (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    -- Classification by stable code, not by a mutable translated label.
    exposes_in_loans BOOLEAN NOT NULL DEFAULT FALSE,
    CHECK (exposes_in_loans = (code = 'partner'))
);
CREATE UNIQUE INDEX one_partner_group ON client_groups(exposes_in_loans)
    WHERE exposes_in_loans;
CREATE TABLE clients (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    party_id BIGINT NOT NULL UNIQUE REFERENCES parties(id),
    group_code TEXT REFERENCES client_groups(code),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX clients_by_group ON clients(group_code, id);
CREATE TABLE client_chat_links (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES clients(id),
    telegram_chat_id BIGINT NOT NULL UNIQUE CHECK (telegram_chat_id <> 0),
    invite_link TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE business_client_bindings (
    purpose TEXT PRIMARY KEY CHECK (purpose IN ('daniyar_balance','best_change_buffer')),
    client_id BIGINT NOT NULL UNIQUE REFERENCES clients(id)
);

-- Accounts are the only source of money balances. Normal side determines display sign.
CREATE TABLE ledger_accounts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    account_class TEXT NOT NULL CHECK (account_class IN ('asset','liability','equity','income','expense')),
    purpose TEXT NOT NULL,
    normal_side TEXT NOT NULL CHECK (normal_side IN ('debit','credit')),
    party_id BIGINT REFERENCES parties(id),
    currency_code TEXT NOT NULL REFERENCES currencies(code),
    instrument_code TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    FOREIGN KEY (instrument_code, currency_code) REFERENCES instruments(code, currency_code),
    UNIQUE (id, currency_code),
    UNIQUE (id, instrument_code)
);
CREATE TABLE cash_accounts (
    instrument_code TEXT PRIMARY KEY REFERENCES instruments(code),
    ledger_account_id BIGINT NOT NULL UNIQUE,
    FOREIGN KEY (ledger_account_id, instrument_code) REFERENCES ledger_accounts(id, instrument_code)
);
CREATE TABLE client_wallet_accounts (
    client_id BIGINT NOT NULL REFERENCES clients(id),
    instrument_code TEXT NOT NULL REFERENCES instruments(code),
    ledger_account_id BIGINT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'client' CHECK (kind IN ('client','profit_buffer')),
    FOREIGN KEY (ledger_account_id, instrument_code) REFERENCES ledger_accounts(id, instrument_code),
    PRIMARY KEY (client_id, instrument_code)
);
CREATE TABLE financial_operations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL,
    posting_mode TEXT NOT NULL CHECK (posting_mode IN ('ledger','memorandum')),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted','cancelled')),
    business_date DATE NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by BIGINT NOT NULL REFERENCES users(id),
    idempotency_key TEXT NOT NULL UNIQUE CHECK (btrim(idempotency_key) <> ''),
    command_hash TEXT NOT NULL CHECK (command_hash ~ '^[a-f0-9]{64}$'),
    reason TEXT,
    reversal_of_id BIGINT UNIQUE REFERENCES financial_operations(id),
    CHECK (reversal_of_id IS NULL OR btrim(coalesce(reason, '')) <> ''),
    CHECK (reversal_of_id IS NULL OR reversal_of_id <> id)
);
CREATE INDEX operations_by_date ON financial_operations(business_date, id);
CREATE TABLE fx_rate_snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    currency_code TEXT NOT NULL REFERENCES currencies(code),
    instrument_code TEXT,
    FOREIGN KEY (instrument_code, currency_code) REFERENCES instruments(code, currency_code),
    quoted_direction TEXT NOT NULL CHECK (quoted_direction IN ('usdt_per_unit','units_per_usdt')),
    quoted_rate positive_rate NOT NULL,
    usdt_per_unit positive_rate NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    created_by BIGINT NOT NULL REFERENCES users(id),
    CHECK ((quoted_direction = 'usdt_per_unit' AND usdt_per_unit = quoted_rate)
        OR (quoted_direction = 'units_per_usdt' AND usdt_per_unit = round(1 / quoted_rate, 18))),
    CHECK (currency_code <> 'USDT' OR (quoted_rate = 1 AND usdt_per_unit = 1))
);
CREATE TABLE journal_lines (
    operation_id BIGINT NOT NULL REFERENCES financial_operations(id),
    line_number SMALLINT NOT NULL CHECK (line_number > 0),
    account_id BIGINT NOT NULL,
    currency_code TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('debit','credit')),
    native_amount quantity NOT NULL CHECK (native_amount >= 0),
    amount_usdt usdt_amount NOT NULL CHECK (amount_usdt >= 0),
    valuation_basis TEXT NOT NULL CHECK (valuation_basis IN ('rate','carrying_cost','historical','base')),
    rate_snapshot_id BIGINT REFERENCES fx_rate_snapshots(id),
    CHECK (native_amount > 0 OR amount_usdt > 0),
    CHECK (valuation_basis <> 'rate' OR rate_snapshot_id IS NOT NULL),
    FOREIGN KEY (account_id, currency_code) REFERENCES ledger_accounts(id, currency_code),
    PRIMARY KEY (operation_id, line_number)
);
CREATE INDEX journal_by_account ON journal_lines(account_id, operation_id, line_number);
CREATE TABLE account_balances (
    account_id BIGINT PRIMARY KEY REFERENCES ledger_accounts(id),
    debit_signed_native quantity NOT NULL DEFAULT 0,
    debit_signed_usdt usdt_amount NOT NULL DEFAULT 0,
    version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0)
);
-- Query source: exact totals of immutable journal; no projection drift or loan duplication.
CREATE VIEW posted_account_balances AS
    SELECT a.id AS account_id, a.currency_code, a.instrument_code,
           coalesce(sum(CASE WHEN l.side = a.normal_side THEN l.native_amount ELSE -l.native_amount END), 0) AS balance,
           coalesce(sum(CASE WHEN l.side = a.normal_side THEN l.amount_usdt ELSE -l.amount_usdt END), 0) AS carrying_usdt
    FROM ledger_accounts a
    LEFT JOIN journal_lines l ON l.account_id = a.id
        AND EXISTS (SELECT 1 FROM financial_operations o WHERE o.id = l.operation_id AND o.status = 'posted')
    GROUP BY a.id;
CREATE VIEW partner_loan_balances AS
    SELECT c.id AS client_id, c.party_id, w.instrument_code, b.currency_code,
           b.balance, b.carrying_usdt, w.ledger_account_id
    FROM clients c
    JOIN client_groups g ON g.code = c.group_code AND g.exposes_in_loans
    JOIN client_wallet_accounts w ON w.client_id = c.id
    JOIN posted_account_balances b ON b.account_id = w.ledger_account_id
    WHERE c.is_active;
CREATE TABLE inventory_movements (
    operation_id BIGINT NOT NULL,
    line_number SMALLINT NOT NULL,
    instrument_code TEXT NOT NULL REFERENCES instruments(code),
    quantity_delta quantity NOT NULL,
    cost_delta_usdt usdt_amount NOT NULL,
    quantity_after quantity NOT NULL CHECK (quantity_after >= 0),
    cost_after_usdt usdt_amount NOT NULL CHECK (cost_after_usdt >= 0),
    PRIMARY KEY (operation_id, line_number),
    FOREIGN KEY (operation_id, line_number) REFERENCES journal_lines(operation_id, line_number),
    CHECK (quantity_delta <> 0 OR cost_delta_usdt <> 0),
    CHECK (quantity_after <> 0 OR cost_after_usdt = 0)
);
CREATE TABLE inventory_balances (
    instrument_code TEXT PRIMARY KEY REFERENCES instruments(code),
    quantity quantity NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    cost_usdt usdt_amount NOT NULL DEFAULT 0 CHECK (cost_usdt >= 0),
    version BIGINT NOT NULL DEFAULT 0,
    CHECK (quantity <> 0 OR cost_usdt = 0)
);

CREATE TABLE capital_agreements (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    investor_party_id BIGINT NOT NULL REFERENCES parties(id),
    principal_kind TEXT NOT NULL DEFAULT 'investor_liability'
        CHECK (principal_kind IN ('owner_equity','investor_liability')),
    opened_on DATE NOT NULL,
    closed_on DATE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CHECK (closed_on IS NULL OR closed_on >= opened_on)
);
CREATE TABLE capital_principal_accounts (
    agreement_id BIGINT NOT NULL REFERENCES capital_agreements(id),
    currency_code TEXT NOT NULL REFERENCES currencies(code),
    ledger_account_id BIGINT NOT NULL UNIQUE,
    FOREIGN KEY (ledger_account_id, currency_code) REFERENCES ledger_accounts(id, currency_code),
    PRIMARY KEY (agreement_id, currency_code)
);
-- Percentage has ONE authoritative versioned home; agreement queries expose it by date.
CREATE TABLE capital_interest_terms (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agreement_id BIGINT NOT NULL REFERENCES capital_agreements(id),
    monthly_interest_percent percentage NOT NULL,
    valid_from DATE NOT NULL,
    valid_until DATE,
    created_by BIGINT NOT NULL REFERENCES users(id),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    UNIQUE (agreement_id, valid_from),
    UNIQUE (id, agreement_id)
);
CREATE TABLE capital_contributions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agreement_id BIGINT NOT NULL REFERENCES capital_agreements(id),
    operation_id BIGINT NOT NULL UNIQUE REFERENCES financial_operations(id),
    instrument_code TEXT NOT NULL REFERENCES instruments(code),
    quantity quantity NOT NULL CHECK (quantity > 0),
    rate_snapshot_id BIGINT NOT NULL REFERENCES fx_rate_snapshots(id),
    principal_usdt usdt_amount NOT NULL CHECK (principal_usdt > 0),
    UNIQUE (id, agreement_id)
);
CREATE INDEX contributions_fifo ON capital_contributions(agreement_id, operation_id, id);
CREATE TABLE capital_withdrawals (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agreement_id BIGINT NOT NULL REFERENCES capital_agreements(id),
    operation_id BIGINT NOT NULL UNIQUE REFERENCES financial_operations(id),
    payout_instrument_code TEXT NOT NULL REFERENCES instruments(code),
    payout_quantity quantity NOT NULL CHECK (payout_quantity > 0),
    principal_usdt usdt_amount NOT NULL CHECK (principal_usdt > 0),
    payout_rate_snapshot_id BIGINT REFERENCES fx_rate_snapshots(id),
    UNIQUE (id, agreement_id)
);
CREATE TABLE capital_withdrawal_allocations (
    withdrawal_id BIGINT NOT NULL,
    contribution_id BIGINT NOT NULL,
    agreement_id BIGINT NOT NULL,
    source_quantity quantity NOT NULL CHECK (source_quantity > 0),
    principal_usdt usdt_amount NOT NULL CHECK (principal_usdt > 0),
    FOREIGN KEY (withdrawal_id, agreement_id) REFERENCES capital_withdrawals(id, agreement_id),
    FOREIGN KEY (contribution_id, agreement_id) REFERENCES capital_contributions(id, agreement_id),
    PRIMARY KEY (withdrawal_id, contribution_id)
);
CREATE INDEX withdrawals_by_contribution ON capital_withdrawal_allocations(contribution_id);
CREATE INDEX withdrawals_by_agreement ON capital_withdrawals(agreement_id, operation_id);
CREATE INDEX agreements_by_investor ON capital_agreements(investor_party_id, id);
CREATE TABLE investor_interest_accruals (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agreement_id BIGINT NOT NULL REFERENCES capital_agreements(id),
    term_id BIGINT NOT NULL,
    period DATE NOT NULL CHECK (extract(day FROM period) = 1),
    basis_as_of DATE NOT NULL,
    basis_usdt usdt_amount NOT NULL CHECK (basis_usdt >= 0),
    calculated_usdt usdt_amount NOT NULL CHECK (calculated_usdt >= 0),
    adjustment_usdt usdt_amount NOT NULL DEFAULT 0,
    final_usdt usdt_amount GENERATED ALWAYS AS (calculated_usdt + adjustment_usdt) STORED,
    adjustment_reason TEXT,
    operation_id BIGINT NOT NULL UNIQUE REFERENCES financial_operations(id),
    FOREIGN KEY (term_id, agreement_id) REFERENCES capital_interest_terms(id, agreement_id),
    CHECK (calculated_usdt + adjustment_usdt >= 0),
    CHECK (adjustment_usdt = 0 OR btrim(coalesce(adjustment_reason, '')) <> '')
);
CREATE INDEX interest_by_period ON investor_interest_accruals(agreement_id, period);

-- Common payout fact for investor interest and owner profit; allocations give its purpose.
CREATE TABLE distribution_payments (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recipient_party_id BIGINT NOT NULL REFERENCES parties(id),
    operation_id BIGINT NOT NULL UNIQUE REFERENCES financial_operations(id),
    instrument_code TEXT NOT NULL REFERENCES instruments(code),
    quantity quantity NOT NULL CHECK (quantity > 0),
    discharged_usdt usdt_amount NOT NULL CHECK (discharged_usdt > 0),
    rate_snapshot_id BIGINT NOT NULL REFERENCES fx_rate_snapshots(id)
);
CREATE TABLE investor_interest_payment_allocations (
    payment_id BIGINT NOT NULL REFERENCES distribution_payments(id),
    accrual_id BIGINT NOT NULL REFERENCES investor_interest_accruals(id),
    amount_usdt usdt_amount NOT NULL CHECK (amount_usdt > 0),
    PRIMARY KEY (payment_id, accrual_id)
);
CREATE INDEX interest_payments_by_accrual ON investor_interest_payment_allocations(accrual_id);

CREATE TABLE expense_categories (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE expenses (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id BIGINT NOT NULL UNIQUE REFERENCES financial_operations(id),
    category_id BIGINT NOT NULL REFERENCES expense_categories(id),
    currency_code TEXT NOT NULL REFERENCES currencies(code),
    amount quantity NOT NULL CHECK (amount > 0),
    amount_usdt usdt_amount NOT NULL CHECK (amount_usdt > 0),
    rate_snapshot_id BIGINT NOT NULL REFERENCES fx_rate_snapshots(id),
    payer_kind TEXT NOT NULL CHECK (payer_kind IN ('party','cash')),
    payer_party_id BIGINT REFERENCES parties(id),
    cash_instrument_code TEXT REFERENCES cash_accounts(instrument_code),
    comment TEXT,
    CHECK ((payer_kind = 'party' AND payer_party_id IS NOT NULL AND cash_instrument_code IS NULL)
        OR (payer_kind = 'cash' AND payer_party_id IS NULL AND cash_instrument_code IS NOT NULL)),
    UNIQUE (id, currency_code)
);
CREATE TABLE expense_share_inputs (
    expense_id BIGINT NOT NULL REFERENCES expenses(id),
    target_code TEXT NOT NULL CHECK (target_code IN ('daniyar','skyex','pe','common')),
    percent percentage NOT NULL,
    PRIMARY KEY (expense_id, target_code)
);
CREATE TABLE expense_allocations (
    expense_id BIGINT NOT NULL REFERENCES expenses(id),
    party_id BIGINT NOT NULL REFERENCES parties(id),
    percent percentage NOT NULL,
    amount quantity NOT NULL CHECK (amount >= 0),
    amount_usdt usdt_amount NOT NULL CHECK (amount_usdt >= 0),
    PRIMARY KEY (expense_id, party_id)
);
-- Party-to-party obligations are managerial claims; only business-facing claims enter its GL.
CREATE TABLE expense_obligations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    expense_id BIGINT NOT NULL,
    currency_code TEXT NOT NULL,
    debtor_party_id BIGINT NOT NULL REFERENCES parties(id),
    creditor_party_id BIGINT NOT NULL REFERENCES parties(id),
    amount quantity NOT NULL CHECK (amount > 0),
    original_usdt usdt_amount NOT NULL CHECK (original_usdt > 0),
    business_ledger_account_id BIGINT REFERENCES ledger_accounts(id),
    FOREIGN KEY (expense_id, currency_code) REFERENCES expenses(id, currency_code),
    CHECK (debtor_party_id <> creditor_party_id),
    UNIQUE (expense_id, debtor_party_id, creditor_party_id)
);
CREATE INDEX obligations_by_parties ON expense_obligations(debtor_party_id, creditor_party_id, currency_code);
CREATE TABLE expense_resolutions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id BIGINT NOT NULL UNIQUE REFERENCES financial_operations(id),
    kind TEXT NOT NULL CHECK (kind IN ('payment','netting')),
    currency_code TEXT NOT NULL REFERENCES currencies(code),
    amount quantity NOT NULL CHECK (amount > 0),
    payer_party_id BIGINT NOT NULL REFERENCES parties(id),
    recipient_party_id BIGINT NOT NULL REFERENCES parties(id),
    method TEXT NOT NULL CHECK (method IN ('direct','cash','wallet','netting')),
    CHECK (payer_party_id <> recipient_party_id),
    CHECK ((kind = 'netting') = (method = 'netting'))
);
CREATE TABLE expense_resolution_allocations (
    resolution_id BIGINT NOT NULL REFERENCES expense_resolutions(id),
    obligation_id BIGINT NOT NULL REFERENCES expense_obligations(id),
    direction TEXT NOT NULL CHECK (direction IN ('forward','reverse')),
    amount quantity NOT NULL CHECK (amount > 0),
    PRIMARY KEY (resolution_id, obligation_id)
);
CREATE INDEX resolutions_by_obligation ON expense_resolution_allocations(obligation_id);
CREATE TABLE expense_period_closures (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period DATE NOT NULL CHECK (extract(day FROM period) = 1),
    revision INTEGER NOT NULL CHECK (revision > 0),
    operation_id BIGINT NOT NULL UNIQUE REFERENCES financial_operations(id),
    supersedes_id BIGINT UNIQUE REFERENCES expense_period_closures(id),
    UNIQUE (period, revision)
);
CREATE TABLE expense_statement_rows (
    closure_id BIGINT NOT NULL REFERENCES expense_period_closures(id),
    debtor_party_id BIGINT NOT NULL REFERENCES parties(id),
    creditor_party_id BIGINT NOT NULL REFERENCES parties(id),
    currency_code TEXT NOT NULL REFERENCES currencies(code),
    opening quantity NOT NULL CHECK (opening >= 0),
    accrued quantity NOT NULL CHECK (accrued >= 0),
    settled quantity NOT NULL CHECK (settled >= 0),
    netted quantity NOT NULL CHECK (netted >= 0),
    closing quantity NOT NULL CHECK (closing >= 0),
    CHECK (closing = opening + accrued - settled - netted),
    CHECK (debtor_party_id <> creditor_party_id),
    PRIMARY KEY (closure_id, debtor_party_id, creditor_party_id, currency_code)
);

CREATE TABLE deals (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id BIGINT REFERENCES clients(id),
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','ready','posted','cancelled')),
    operation_id BIGINT UNIQUE REFERENCES financial_operations(id),
    external_reference TEXT,
    created_by BIGINT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (status <> 'posted' OR operation_id IS NOT NULL),
    UNIQUE (source, external_reference)
);
CREATE TABLE deal_legs (
    deal_id BIGINT NOT NULL REFERENCES deals(id),
    number SMALLINT NOT NULL CHECK (number > 0),
    direction TEXT NOT NULL CHECK (direction IN ('receive','pay','fee')),
    instrument_code TEXT NOT NULL REFERENCES instruments(code),
    quantity quantity NOT NULL CHECK (quantity > 0),
    rate_snapshot_id BIGINT REFERENCES fx_rate_snapshots(id),
    PRIMARY KEY (deal_id, number)
);
CREATE TABLE best_change_calculations (
    deal_id BIGINT PRIMARY KEY REFERENCES deals(id),
    pricing_currency TEXT NOT NULL REFERENCES currencies(code),
    quantity_usdt quantity NOT NULL CHECK (quantity_usdt > 0),
    market_rate positive_rate NOT NULL,
    client_rate positive_rate NOT NULL,
    commission_percent percentage NOT NULL,
    gross_spread quantity NOT NULL,
    platform_fee_usdt usdt_amount NOT NULL CHECK (platform_fee_usdt >= 0),
    net_pool_usdt usdt_amount NOT NULL,
    daniyar_share_usdt usdt_amount NOT NULL,
    business_share_usdt usdt_amount NOT NULL,
    valuation_rate_id BIGINT NOT NULL REFERENCES fx_rate_snapshots(id),
    calculation_version TEXT NOT NULL,
    buffer_wallet_account_id BIGINT NOT NULL REFERENCES client_wallet_accounts(ledger_account_id),
    CHECK (net_pool_usdt = daniyar_share_usdt + business_share_usdt)
);
CREATE TABLE best_change_period_transfers (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period DATE NOT NULL CHECK (extract(day FROM period) = 1),
    revision INTEGER NOT NULL CHECK (revision > 0),
    operation_id BIGINT NOT NULL UNIQUE REFERENCES financial_operations(id),
    source_wallet_account_id BIGINT NOT NULL REFERENCES client_wallet_accounts(ledger_account_id),
    target_wallet_account_id BIGINT NOT NULL REFERENCES client_wallet_accounts(ledger_account_id),
    amount_usdt usdt_amount NOT NULL,
    CHECK (source_wallet_account_id <> target_wallet_account_id),
    CHECK (amount_usdt <> 0),
    UNIQUE (period, source_wallet_account_id, revision)
);
CREATE TABLE best_change_transfer_allocations (
    transfer_id BIGINT NOT NULL REFERENCES best_change_period_transfers(id),
    deal_id BIGINT NOT NULL REFERENCES best_change_calculations(deal_id),
    amount_usdt usdt_amount NOT NULL,
    PRIMARY KEY (transfer_id, deal_id)
);
CREATE INDEX transfers_by_best_change_deal ON best_change_transfer_allocations(deal_id);
CREATE TABLE profit_share_terms (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    valid_from DATE NOT NULL UNIQUE,
    valid_until DATE,
    skyex_percent percentage NOT NULL,
    pe_percent percentage NOT NULL,
    created_by BIGINT NOT NULL REFERENCES users(id),
    CHECK (skyex_percent + pe_percent = 100),
    CHECK (valid_until IS NULL OR valid_until > valid_from)
);
CREATE TABLE profit_period_closures (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period DATE NOT NULL CHECK (extract(day FROM period) = 1),
    revision INTEGER NOT NULL CHECK (revision > 0),
    operation_id BIGINT NOT NULL UNIQUE REFERENCES financial_operations(id),
    share_term_id BIGINT NOT NULL REFERENCES profit_share_terms(id),
    income_usdt usdt_amount NOT NULL,
    business_expenses_usdt usdt_amount NOT NULL CHECK (business_expenses_usdt >= 0),
    investor_interest_usdt usdt_amount NOT NULL CHECK (investor_interest_usdt >= 0),
    net_profit_usdt usdt_amount GENERATED ALWAYS AS
        (income_usdt - business_expenses_usdt - investor_interest_usdt) STORED,
    supersedes_id BIGINT UNIQUE REFERENCES profit_period_closures(id),
    UNIQUE (period, revision)
);
CREATE TABLE partner_profit_accruals (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    closure_id BIGINT NOT NULL REFERENCES profit_period_closures(id),
    owner_party_id BIGINT NOT NULL REFERENCES parties(id),
    amount_usdt usdt_amount NOT NULL,
    UNIQUE (closure_id, owner_party_id)
);
CREATE TABLE partner_profit_payment_allocations (
    payment_id BIGINT NOT NULL REFERENCES distribution_payments(id),
    accrual_id BIGINT NOT NULL REFERENCES partner_profit_accruals(id),
    amount_usdt usdt_amount NOT NULL CHECK (amount_usdt > 0),
    PRIMARY KEY (payment_id, accrual_id)
);
CREATE TABLE period_locks (
    context TEXT NOT NULL CHECK (context IN ('ledger','expenses','interest','profit')),
    period DATE NOT NULL CHECK (extract(day FROM period) = 1),
    closed_by_operation_id BIGINT REFERENCES financial_operations(id),
    PRIMARY KEY (context, period)
);
CREATE TABLE telegram_outbox (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id BIGINT REFERENCES financial_operations(id),
    event_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','sent','failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until TIMESTAMPTZ,
    last_error TEXT
);
CREATE INDEX outbox_pending ON telegram_outbox(available_at, id) WHERE status IN ('pending','processing');

-- DB guardrails for the prototype. Other cross-document policies are specified in MODEL.md.
CREATE FUNCTION assert_balanced_operation() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE
    op financial_operations%ROWTYPE;
    target_id BIGINT;
    line_count INTEGER;
    signed_total NUMERIC;
BEGIN
    IF TG_TABLE_NAME = 'financial_operations' THEN
        target_id := coalesce(NEW.id, OLD.id);
    ELSE
        target_id := coalesce(NEW.operation_id, OLD.operation_id);
    END IF;
    SELECT * INTO op FROM financial_operations WHERE id = target_id;
    IF NOT FOUND OR op.status <> 'posted' THEN RETURN NULL; END IF;
    SELECT count(*), coalesce(sum(CASE side WHEN 'debit' THEN amount_usdt ELSE -amount_usdt END), 0)
        INTO line_count, signed_total FROM journal_lines WHERE operation_id = target_id;
    IF (op.posting_mode = 'ledger' AND (line_count < 2 OR signed_total <> 0))
       OR (op.posting_mode = 'memorandum' AND line_count <> 0) THEN
        RAISE EXCEPTION 'Operation % is not balanced for its posting mode', target_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER operation_balance_check
    AFTER INSERT OR UPDATE ON financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_balanced_operation();
CREATE CONSTRAINT TRIGGER journal_balance_check
    AFTER INSERT OR UPDATE OR DELETE ON journal_lines DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_balanced_operation();

CREATE FUNCTION protect_posted_operation() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF OLD.status = 'posted' THEN
        RAISE EXCEPTION 'Posted operation is immutable; create a reversal' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER protect_operation BEFORE UPDATE OR DELETE ON financial_operations
    FOR EACH ROW EXECUTE FUNCTION protect_posted_operation();
CREATE FUNCTION protect_posted_journal() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE current_status TEXT;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        SELECT status INTO current_status FROM financial_operations
            WHERE id = OLD.operation_id FOR UPDATE;
        IF current_status = 'posted' THEN RAISE EXCEPTION 'Posted journal is immutable' USING ERRCODE = '23514'; END IF;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        SELECT status INTO current_status FROM financial_operations
            WHERE id = NEW.operation_id FOR UPDATE;
        IF current_status = 'posted' THEN RAISE EXCEPTION 'Cannot append to a posted journal' USING ERRCODE = '23514'; END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER protect_journal BEFORE INSERT OR UPDATE OR DELETE ON journal_lines
    FOR EACH ROW EXECUTE FUNCTION protect_posted_journal();

-- Storage guards. Domain formulas and cross-document total checks follow in E3–E8.
CREATE FUNCTION document_operation_id(table_name TEXT, document JSONB) RETURNS BIGINT
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
DECLARE result BIGINT;
BEGIN
    IF document ? 'operation_id' THEN RETURN (document->>'operation_id')::bigint; END IF;
    CASE table_name
        WHEN 'capital_withdrawal_allocations' THEN
            SELECT operation_id INTO result FROM capital_withdrawals WHERE id=(document->>'withdrawal_id')::bigint;
        WHEN 'investor_interest_payment_allocations','partner_profit_payment_allocations' THEN
            SELECT operation_id INTO result FROM distribution_payments WHERE id=(document->>'payment_id')::bigint;
        WHEN 'expense_share_inputs','expense_allocations','expense_obligations' THEN
            SELECT operation_id INTO result FROM expenses WHERE id=(document->>'expense_id')::bigint;
        WHEN 'expense_resolution_allocations' THEN
            SELECT operation_id INTO result FROM expense_resolutions WHERE id=(document->>'resolution_id')::bigint;
        WHEN 'expense_statement_rows' THEN
            SELECT operation_id INTO result FROM expense_period_closures WHERE id=(document->>'closure_id')::bigint;
        WHEN 'deal_legs','best_change_calculations' THEN
            SELECT operation_id INTO result FROM deals WHERE id=(document->>'deal_id')::bigint;
        WHEN 'best_change_transfer_allocations' THEN
            SELECT operation_id INTO result FROM best_change_period_transfers WHERE id=(document->>'transfer_id')::bigint;
        WHEN 'partner_profit_accruals' THEN
            SELECT operation_id INTO result FROM profit_period_closures WHERE id=(document->>'closure_id')::bigint;
        ELSE RAISE EXCEPTION 'Unsupported financial document %',table_name;
    END CASE;
    RETURN result;
END $$;
CREATE FUNCTION protect_financial_document() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE parent_id BIGINT; parent_status TEXT;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        parent_id := document_operation_id(TG_TABLE_NAME,to_jsonb(OLD));
        SELECT status INTO parent_status FROM financial_operations WHERE id=parent_id FOR UPDATE;
        IF parent_status='posted' THEN
            RAISE EXCEPTION 'Posted financial document is immutable' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        parent_id := document_operation_id(TG_TABLE_NAME,to_jsonb(NEW));
        SELECT status INTO parent_status FROM financial_operations WHERE id=parent_id FOR UPDATE;
        IF parent_status='posted' THEN
            RAISE EXCEPTION 'Cannot attach to a posted financial document' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
DO $$
DECLARE name TEXT;
BEGIN
    FOREACH name IN ARRAY ARRAY[
        'capital_contributions','capital_withdrawals','capital_withdrawal_allocations',
        'investor_interest_accruals','distribution_payments',
        'investor_interest_payment_allocations','partner_profit_payment_allocations',
        'expenses','expense_share_inputs','expense_allocations','expense_obligations',
        'expense_resolutions','expense_resolution_allocations',
        'expense_period_closures','expense_statement_rows','deals','deal_legs',
        'best_change_calculations','best_change_period_transfers','best_change_transfer_allocations',
        'profit_period_closures','partner_profit_accruals','inventory_movements'
    ] LOOP
        EXECUTE format('CREATE TRIGGER protect_document BEFORE INSERT OR UPDATE OR DELETE ON %I
            FOR EACH ROW EXECUTE FUNCTION protect_financial_document()',name);
    END LOOP;
END $$;
CREATE FUNCTION protect_snapshot() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    RAISE EXCEPTION 'Snapshot is immutable; append a new version' USING ERRCODE='23514';
END $$;
CREATE TRIGGER immutable_rate BEFORE UPDATE OR DELETE ON fx_rate_snapshots
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
CREATE TRIGGER immutable_identity BEFORE UPDATE OR DELETE ON crm_schema_identity
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
-- Terms serialize by agreement; adjacent [from,until) intervals are allowed.
CREATE FUNCTION check_interest_term_interval() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    PERFORM 1 FROM capital_agreements WHERE id=NEW.agreement_id FOR UPDATE;
    IF EXISTS (SELECT 1 FROM capital_interest_terms t WHERE t.agreement_id=NEW.agreement_id
        AND t.id<>NEW.id AND daterange(t.valid_from,t.valid_until,'[)') && daterange(NEW.valid_from,NEW.valid_until,'[)')) THEN
        RAISE EXCEPTION 'Interest term intervals overlap' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER interest_intervals BEFORE INSERT OR UPDATE ON capital_interest_terms
    FOR EACH ROW EXECUTE FUNCTION check_interest_term_interval();
CREATE FUNCTION protect_used_interest_term() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM investor_interest_accruals a JOIN financial_operations o ON o.id=a.operation_id
        WHERE a.term_id=OLD.id AND o.status='posted') THEN
        IF TG_OP='DELETE' OR NEW.agreement_id<>OLD.agreement_id
            OR NEW.monthly_interest_percent<>OLD.monthly_interest_percent OR NEW.valid_from<>OLD.valid_from
            OR NEW.created_by<>OLD.created_by THEN
            RAISE EXCEPTION 'Used interest terms are immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.valid_until IS DISTINCT FROM OLD.valid_until AND (NEW.valid_until IS NULL OR
            NEW.valid_until <= (now() AT TIME ZONE 'Asia/Bangkok')::date OR EXISTS (
                SELECT 1 FROM investor_interest_accruals a JOIN financial_operations o ON o.id=a.operation_id
                WHERE a.term_id=OLD.id AND o.status='posted'
                  AND NEW.valid_until < (a.period + INTERVAL '1 month')::date)) THEN
            RAISE EXCEPTION 'Cannot change an accrued interest interval' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER used_interest_terms BEFORE UPDATE OR DELETE ON capital_interest_terms
    FOR EACH ROW EXECUTE FUNCTION protect_used_interest_term();

CREATE FUNCTION check_profit_term_interval() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(740810,2);
    IF EXISTS (SELECT 1 FROM profit_share_terms t WHERE t.id<>NEW.id
        AND daterange(t.valid_from,t.valid_until,'[)') && daterange(NEW.valid_from,NEW.valid_until,'[)')) THEN
        RAISE EXCEPTION 'Profit share intervals overlap' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER profit_intervals BEFORE INSERT OR UPDATE ON profit_share_terms
    FOR EACH ROW EXECUTE FUNCTION check_profit_term_interval();
CREATE FUNCTION protect_used_profit_term() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM profit_period_closures c JOIN financial_operations o ON o.id=c.operation_id
        WHERE c.share_term_id=OLD.id AND o.status='posted') THEN
        IF TG_OP='DELETE' OR NEW.valid_from<>OLD.valid_from OR NEW.skyex_percent<>OLD.skyex_percent
            OR NEW.pe_percent<>OLD.pe_percent OR NEW.created_by<>OLD.created_by THEN
            RAISE EXCEPTION 'Used profit terms are immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.valid_until IS DISTINCT FROM OLD.valid_until AND (NEW.valid_until IS NULL OR
            NEW.valid_until <= (now() AT TIME ZONE 'Asia/Bangkok')::date OR EXISTS (
                SELECT 1 FROM profit_period_closures c JOIN financial_operations o ON o.id=c.operation_id
                WHERE c.share_term_id=OLD.id AND o.status='posted'
                  AND NEW.valid_until < (c.period + INTERVAL '1 month')::date)) THEN
            RAISE EXCEPTION 'Cannot change a closed profit interval' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER used_profit_terms BEFORE UPDATE OR DELETE ON profit_share_terms
    FOR EACH ROW EXECUTE FUNCTION protect_used_profit_term();

-- A debt currency and the actual payment currency can differ, with a stored quote.
ALTER TABLE expense_resolutions ADD COLUMN payment_instrument_code TEXT REFERENCES instruments(code);
ALTER TABLE expense_resolutions ADD COLUMN payment_quantity quantity CHECK (payment_quantity > 0);
ALTER TABLE expense_resolutions ADD COLUMN payment_rate_snapshot_id BIGINT REFERENCES fx_rate_snapshots(id);
ALTER TABLE expense_resolutions ADD COLUMN wallet_account_id BIGINT REFERENCES client_wallet_accounts(ledger_account_id);
ALTER TABLE expense_resolutions ADD CONSTRAINT resolution_payment_shape CHECK (
    (method='netting' AND payment_instrument_code IS NULL AND payment_quantity IS NULL
        AND payment_rate_snapshot_id IS NULL AND wallet_account_id IS NULL)
    OR (method='direct' AND wallet_account_id IS NULL)
    OR (method='cash' AND payment_instrument_code IS NOT NULL AND payment_quantity IS NOT NULL
        AND payment_rate_snapshot_id IS NOT NULL AND wallet_account_id IS NULL)
    OR (method='wallet' AND payment_instrument_code IS NOT NULL AND payment_quantity IS NOT NULL
        AND payment_rate_snapshot_id IS NOT NULL AND wallet_account_id IS NOT NULL)
);
ALTER TABLE partner_profit_accruals ADD CONSTRAINT positive_profit_distribution CHECK (amount_usdt > 0);

CREATE FUNCTION check_physical_cash() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    -- Serialize all cash posting checks in a stable account order.
    PERFORM a.id FROM ledger_accounts a JOIN cash_accounts c ON c.ledger_account_id=a.id
        ORDER BY a.id FOR UPDATE OF a;
    IF EXISTS (SELECT 1 FROM cash_accounts c JOIN posted_account_balances b ON b.account_id=c.ledger_account_id
        WHERE b.balance<0 OR b.carrying_usdt<0) THEN
        RAISE EXCEPTION 'Physical cash cannot be negative' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER nonnegative_cash AFTER INSERT OR UPDATE ON financial_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_physical_cash();

-- Technical entities bind to Thai operations/deals, never legacy transactions.
CREATE TABLE access_roles (
    code TEXT PRIMARY KEY CHECK (code IN ('cashier','manager','accountant','owner','admin')),
    name TEXT NOT NULL
);
ALTER TABLE user_roles ADD FOREIGN KEY (role) REFERENCES access_roles(code);
CREATE TABLE role_permissions (
    role TEXT NOT NULL REFERENCES access_roles(code),
    permission TEXT NOT NULL,
    PRIMARY KEY (role,permission)
);
CREATE TABLE crm_settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL
);
CREATE TABLE exchange_requests (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    external_reference TEXT NOT NULL UNIQUE,
    client_id BIGINT NOT NULL REFERENCES clients(id),
    deal_id BIGINT UNIQUE REFERENCES deals(id),
    status TEXT NOT NULL CHECK (status IN ('draft','ready','posted','cancelled')),
    created_by BIGINT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_text TEXT,
    CHECK (status<>'posted' OR deal_id IS NOT NULL)
);
CREATE TABLE telegram_message_links (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id BIGINT REFERENCES exchange_requests(id),
    deal_id BIGINT REFERENCES deals(id),
    operation_id BIGINT REFERENCES financial_operations(id),
    chat_id BIGINT NOT NULL CHECK (chat_id<>0),
    message_id BIGINT NOT NULL CHECK (message_id>0),
    purpose TEXT NOT NULL,
    UNIQUE (chat_id,message_id)
);
CREATE TABLE payment_watches (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id BIGINT REFERENCES deals(id),
    chat_id BIGINT NOT NULL CHECK (chat_id<>0),
    reply_message_id BIGINT NOT NULL,
    address TEXT NOT NULL,
    our_address TEXT NOT NULL,
    created_by_user_id BIGINT REFERENCES users(id),
    mode TEXT NOT NULL CHECK (mode IN ('SINGLE','TEST_THEN_MAIN')),
    phase TEXT NOT NULL CHECK (phase IN ('TEST','MAIN')),
    status TEXT NOT NULL CHECK (status IN ('WATCHING','TIMED_OUT','COMPLETED','STOPPED')),
    timeout_at TIMESTAMPTZ NOT NULL,
    continue_count INTEGER NOT NULL DEFAULT 0 CHECK (continue_count>=0),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_checked_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    stopped_at TIMESTAMPTZ,
    timed_out_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notice_message_id BIGINT,
    chat_name TEXT
);
CREATE INDEX watches_pending ON payment_watches(timeout_at,id) WHERE status='WATCHING';
CREATE INDEX watches_by_deal ON payment_watches(deal_id);
CREATE TABLE payment_watch_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    watch_id BIGINT NOT NULL REFERENCES payment_watches(id),
    tx_hash TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL CHECK (event_type IN ('TEST','MAIN')),
    direction TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
    amount quantity NOT NULL CHECK (amount>0),
    token_symbol TEXT NOT NULL,
    confirmations INTEGER NOT NULL CHECK (confirmations>=0),
    block_ts TIMESTAMPTZ NOT NULL,
    operation_id BIGINT UNIQUE REFERENCES financial_operations(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (watch_id,tx_hash)
);
CREATE INDEX watch_events_by_watch ON payment_watch_events(watch_id,created_at,id);

-- Independent message archive; no links to financial history.
CREATE TABLE message_archive_chats (
    id BIGSERIAL PRIMARY KEY,
    telegram_chat_id BIGINT,
    desktop_chat_id BIGINT,
    chat_type TEXT NOT NULL,
    current_title TEXT NOT NULL,
    username TEXT,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    first_message_at TIMESTAMPTZ,
    last_message_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX uq_message_archive_chats_telegram
    ON message_archive_chats(telegram_chat_id) WHERE telegram_chat_id IS NOT NULL;
CREATE UNIQUE INDEX uq_message_archive_chats_desktop
    ON message_archive_chats(desktop_chat_id) WHERE desktop_chat_id IS NOT NULL;
CREATE INDEX ix_message_archive_chats_title
    ON message_archive_chats(LOWER(current_title));

CREATE TABLE message_archive_chat_names (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL REFERENCES message_archive_chats(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chat_id, normalized_name)
);
CREATE INDEX ix_message_archive_chat_names_normalized
    ON message_archive_chat_names(normalized_name);

CREATE TABLE message_archive_authors (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT,
    desktop_source_key TEXT,
    display_name TEXT NOT NULL,
    username TEXT,
    is_bot BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX uq_message_archive_authors_telegram
    ON message_archive_authors(telegram_user_id) WHERE telegram_user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_message_archive_authors_desktop
    ON message_archive_authors(desktop_source_key) WHERE desktop_source_key IS NOT NULL;

CREATE TABLE message_archive_messages (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL REFERENCES message_archive_chats(id) ON DELETE RESTRICT,
    telegram_message_id BIGINT NOT NULL,
    author_id BIGINT REFERENCES message_archive_authors(id) ON DELETE RESTRICT,
    message_type TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound', 'imported')),
    sent_at TIMESTAMPTZ NOT NULL,
    edited_at TIMESTAMPTZ,
    reply_to_message_id BIGINT,
    media_group_id TEXT,
    text_plain TEXT,
    text_entities JSONB NOT NULL DEFAULT '[]'::jsonb,
    forward_info JSONB,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    source TEXT NOT NULL CHECK (source IN ('bot_api', 'telegram_desktop')),
    revision_no INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chat_id, telegram_message_id)
);
CREATE INDEX ix_message_archive_messages_chat_time
    ON message_archive_messages(chat_id, sent_at, telegram_message_id);
CREATE INDEX ix_message_archive_messages_chat_author_time
    ON message_archive_messages(chat_id, author_id, sent_at);

CREATE TABLE message_archive_revisions (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL REFERENCES message_archive_messages(id) ON DELETE RESTRICT,
    revision_no INTEGER NOT NULL,
    text_plain TEXT,
    text_entities JSONB NOT NULL DEFAULT '[]'::jsonb,
    edited_at TIMESTAMPTZ NOT NULL,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (message_id, revision_no),
    UNIQUE (message_id, content_hash)
);

CREATE TABLE message_archive_attachments (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL REFERENCES message_archive_messages(id) ON DELETE RESTRICT,
    attachment_type TEXT NOT NULL,
    ordinal SMALLINT NOT NULL DEFAULT 0,
    telegram_file_id TEXT,
    telegram_file_unique_id TEXT,
    mime_type TEXT,
    original_name TEXT,
    file_size BIGINT,
    duration_seconds INTEGER,
    width INTEGER,
    height INTEGER,
    storage_key TEXT,
    sha256 TEXT,
    download_status TEXT NOT NULL DEFAULT 'skipped'
        CHECK (download_status IN ('pending', 'ready', 'skipped', 'failed', 'unavailable')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    download_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (message_id, attachment_type, ordinal)
);
CREATE INDEX ix_message_archive_attachments_download
    ON message_archive_attachments(download_status, id)
    WHERE download_status IN ('pending', 'failed');
CREATE INDEX ix_message_archive_attachments_sha256
    ON message_archive_attachments(sha256) WHERE sha256 IS NOT NULL;

CREATE TABLE message_archive_imports (
    id BIGSERIAL PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_checksum TEXT,
    dry_run BOOLEAN NOT NULL,
    status TEXT NOT NULL,
    chats_found INTEGER NOT NULL DEFAULT 0,
    messages_created INTEGER NOT NULL DEFAULT 0,
    messages_updated INTEGER NOT NULL DEFAULT 0,
    messages_skipped INTEGER NOT NULL DEFAULT 0,
    files_missing INTEGER NOT NULL DEFAULT 0,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

CREATE TABLE message_archive_exports (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL REFERENCES message_archive_chats(id) ON DELETE RESTRICT,
    requested_by_user_id BIGINT NOT NULL,
    requested_in_chat_id BIGINT NOT NULL,
    status TEXT NOT NULL,
    message_count BIGINT NOT NULL DEFAULT 0,
    part_count INTEGER NOT NULL DEFAULT 0,
    total_size BIGINT NOT NULL DEFAULT 0,
    telegram_result_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

-- New business reference data only. No real operators, clients, chats, FX rates or funds.
INSERT INTO currencies(code,name,precision) VALUES
    ('THB','Тайский бат',2),('USDT','USDT',6),('USD','Доллар США',2),
    ('EUR','Евро',2),('RUB','Российский рубль',2);
INSERT INTO instruments(code,currency_code,name,storage_kind,precision) VALUES
    ('THB_CASH','THB','Наличный бат','cash',2),('THB_BANK','THB','Безналичный бат','bank',2),
    ('USDT','USDT','USDT','crypto',6),('USD_WH','USD','Доллар белый','cash',2),
    ('USD_BL','USD','Доллар синий','cash',2),('EUR','EUR','Евро','cash',2),
    ('EUR500','EUR','Евро 500','cash',2),('RUB','RUB','Рубль','cash',2);
INSERT INTO instrument_aliases(alias,instrument_code) VALUES
    ('thb','THB_CASH'),('thb_cash','THB_CASH'),('бат','THB_CASH'),('батнал','THB_CASH'),
    ('thbb','THB_BANK'),('thb_bank','THB_BANK'),('батбезнал','THB_BANK'),
    ('usdt','USDT'),('юсдт','USDT'),('usdw','USD_WH'),('usd_wh','USD_WH'),('долбел','USD_WH'),
    ('usd','USD_BL'),('usdb','USD_BL'),('usdbl','USD_BL'),('usd_bl','USD_BL'),('долсин','USD_BL'),
    ('eur','EUR'),('евро','EUR'),('eur500','EUR500'),('eur 500','EUR500'),('евро500','EUR500'),
    ('евро 500','EUR500'),('rub','RUB'),('руб','RUB');
INSERT INTO client_groups(code,name,exposes_in_loans) VALUES
    ('balance','Баланс',FALSE),('partner','Партнер',TRUE);
INSERT INTO access_roles(code,name) VALUES
    ('cashier','Кассир'),('manager','Менеджер'),('accountant','Бухгалтер'),
    ('owner','Владелец'),('admin','Администратор');
INSERT INTO role_permissions(role,permission)
    SELECT role,permission FROM unnest(ARRAY['manager','accountant','owner','admin']) AS role
    CROSS JOIN unnest(ARRAY['deal.post','capital.post','expense.post','interest.approve','period.close']) AS permission;
INSERT INTO role_permissions(role,permission)
    SELECT role,permission FROM unnest(ARRAY['owner','admin']) AS role
    CROSS JOIN unnest(ARRAY['access.manage','terms.manage','period.revise']) AS permission;
INSERT INTO role_permissions(role,permission) VALUES ('cashier','cash.execute');
INSERT INTO crm_settings(key,value) VALUES
    ('wallet_negative_policy','"allowed"'),('loss_policy','"report_only"'),
    ('capital_return_policy','"same_currency_same_form_except_thb"'),
    ('investor_shortfall_policy','"manager_decision"');
DO $$
DECLARE business BIGINT; sky BIGINT; pe BIGINT; daniyar BIGINT; bootstrap_actor_id BIGINT;
        business_day DATE := (now() AT TIME ZONE 'Asia/Bangkok')::date;
        investor BIGINT; agreement BIGINT;
BEGIN
    INSERT INTO parties(name,kind) VALUES ('Обменник Таиланд','business') RETURNING id INTO business;
    INSERT INTO parties(name,kind) VALUES ('SkyEX','organization') RETURNING id INTO sky;
    INSERT INTO parties(name,kind) VALUES ('ПЕ','person') RETURNING id INTO pe;
    INSERT INTO parties(name,kind) VALUES ('Данияр','person') RETURNING id INTO daniyar;
    INSERT INTO business_participants(code,party_id) VALUES
        ('business',business),('skyex',sky),('pe',pe),('daniyar',daniyar);
    INSERT INTO party_roles(party_id,role) VALUES
        (sky,'owner'),(sky,'investor'),(pe,'owner'),(pe,'investor');
    -- FK author of initial terms only: inactive, no Telegram ID and no access roles.
    INSERT INTO users(display_name,is_active) VALUES ('System bootstrap',FALSE) RETURNING id INTO bootstrap_actor_id;
    INSERT INTO profit_share_terms(valid_from,skyex_percent,pe_percent,created_by)
        VALUES (business_day,50,50,bootstrap_actor_id);
    FOREACH investor IN ARRAY ARRAY[sky,pe] LOOP
        INSERT INTO capital_agreements(investor_party_id,principal_kind,opened_on)
            VALUES (investor,'owner_equity',business_day) RETURNING id INTO agreement;
        INSERT INTO capital_interest_terms(agreement_id,monthly_interest_percent,valid_from,created_by)
            VALUES (agreement,0,business_day,bootstrap_actor_id);
    END LOOP;
END $$;
INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code,instrument_code)
    SELECT 'cash:'||code,'asset','cash','debit',currency_code,code FROM instruments;
INSERT INTO cash_accounts(instrument_code,ledger_account_id)
    SELECT instrument_code,id FROM ledger_accounts WHERE purpose='cash';
INSERT INTO inventory_balances(instrument_code) SELECT code FROM instruments;
INSERT INTO account_balances(account_id) SELECT id FROM ledger_accounts;
INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code)
    SELECT purpose,account_class,purpose,normal_side,'USDT'
    FROM (VALUES
        ('trading_income','income','credit'),('trading_loss','expense','debit'),
        ('transfer_fee_income','income','credit'),('operating_expense','expense','debit'),
        ('platform_commission_expense','expense','debit'),('investor_interest_expense','expense','debit'),
        ('fx_income','income','credit'),('fx_loss','expense','debit'),
        ('period_result','equity','credit'),('retained_result','equity','credit')
    ) AS accounts(purpose,account_class,normal_side);
INSERT INTO account_balances(account_id)
    SELECT id FROM ledger_accounts WHERE instrument_code IS NULL;

-- Preserve the classification and bindings used by posted money history.
CREATE FUNCTION protect_used_account() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM journal_lines l JOIN financial_operations o ON o.id=l.operation_id
        WHERE l.account_id=OLD.id AND o.status='posted') AND (
        TG_OP='DELETE' OR NEW.id<>OLD.id OR NEW.account_class<>OLD.account_class
        OR NEW.normal_side<>OLD.normal_side OR NEW.purpose<>OLD.purpose
        OR NEW.currency_code<>OLD.currency_code OR NEW.party_id IS DISTINCT FROM OLD.party_id
        OR NEW.instrument_code IS DISTINCT FROM OLD.instrument_code) THEN
        RAISE EXCEPTION 'Used account classification is immutable' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER used_account BEFORE UPDATE OR DELETE ON ledger_accounts
    FOR EACH ROW EXECUTE FUNCTION protect_used_account();
CREATE TRIGGER immutable_cash_binding BEFORE UPDATE OR DELETE ON cash_accounts
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
CREATE TRIGGER immutable_wallet_binding BEFORE UPDATE OR DELETE ON client_wallet_accounts
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();


ALTER TABLE financial_operations ADD COLUMN context TEXT NOT NULL DEFAULT 'ledger'
    CHECK (context IN ('ledger','expenses','interest','profit'));
ALTER TABLE financial_operations ADD COLUMN command_details JSONB NOT NULL DEFAULT '{}'
    CHECK (jsonb_typeof(command_details)='object');
ALTER TABLE telegram_outbox ADD COLUMN lease_token UUID;
CREATE TABLE operation_audit_notes (
    operation_id BIGINT PRIMARY KEY REFERENCES financial_operations(id),
    note TEXT NOT NULL CHECK (btrim(note)<>'')
);
CREATE TRIGGER immutable_audit_note BEFORE INSERT OR UPDATE OR DELETE ON operation_audit_notes
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE TABLE financial_operation_dependencies (
    source_operation_id BIGINT NOT NULL REFERENCES financial_operations(id),
    dependent_operation_id BIGINT NOT NULL REFERENCES financial_operations(id),
    CHECK (source_operation_id<>dependent_operation_id),
    PRIMARY KEY (source_operation_id,dependent_operation_id)
);
CREATE INDEX dependent_operation_lookup ON financial_operation_dependencies(dependent_operation_id);

-- Normal writers share the barrier; projection rebuild and closing/reopening take it exclusively.
CREATE FUNCTION financial_write_barrier() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    PERFORM pg_advisory_xact_lock_shared(740810,3);
    IF NEW.status='posted' AND EXISTS(SELECT 1 FROM period_locks p
        WHERE p.context IN ('ledger',NEW.context)
        AND p.period=date_trunc('month',NEW.business_date)::date
        AND p.closed_by_operation_id IS DISTINCT FROM NEW.id) THEN
        RAISE EXCEPTION 'Financial period is closed' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER posting_barrier BEFORE INSERT OR UPDATE ON financial_operations
    FOR EACH ROW EXECUTE FUNCTION financial_write_barrier();
CREATE FUNCTION period_write_barrier() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(740810,3);
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER period_barrier BEFORE INSERT OR UPDATE OR DELETE ON period_locks
    FOR EACH ROW EXECUTE FUNCTION period_write_barrier();

-- Lock only affected cash accounts: locking every cash account after holding a subset deadlocks.
CREATE OR REPLACE FUNCTION check_physical_cash() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    PERFORM a.id FROM ledger_accounts a JOIN cash_accounts c ON c.ledger_account_id=a.id
        WHERE EXISTS(SELECT 1 FROM journal_lines l WHERE l.operation_id=NEW.id AND l.account_id=a.id)
        ORDER BY a.id FOR UPDATE OF a;
    IF EXISTS(SELECT 1 FROM cash_accounts c JOIN posted_account_balances b ON b.account_id=c.ledger_account_id
        WHERE (b.balance<0 OR b.carrying_usdt<0) AND EXISTS(
            SELECT 1 FROM journal_lines l WHERE l.operation_id=NEW.id AND l.account_id=c.ledger_account_id)) THEN
        RAISE EXCEPTION 'Physical cash cannot be negative' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE FUNCTION check_journal_precision_and_rate() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE native_precision INTEGER; quote fx_rate_snapshots%ROWTYPE; instrument TEXT;
BEGIN
    SELECT coalesce(i.precision,CASE WHEN a.currency_code='USDT' THEN 8 ELSE c.precision END),a.instrument_code
        INTO native_precision,instrument FROM ledger_accounts a JOIN currencies c ON c.code=a.currency_code
        LEFT JOIN instruments i ON i.code=a.instrument_code WHERE a.id=NEW.account_id;
    IF NEW.native_amount<>round(NEW.native_amount,native_precision)
        OR (NEW.currency_code='USDT' AND NEW.native_amount<>NEW.amount_usdt) THEN
        RAISE EXCEPTION 'Invalid journal amount precision' USING ERRCODE='23514';
    END IF;
    IF NEW.rate_snapshot_id IS NOT NULL THEN
        SELECT * INTO quote FROM fx_rate_snapshots WHERE id=NEW.rate_snapshot_id;
        IF quote.currency_code<>NEW.currency_code OR (quote.instrument_code IS NOT NULL AND quote.instrument_code IS DISTINCT FROM instrument)
            OR (NEW.valuation_basis='rate' AND NEW.amount_usdt<>round(NEW.native_amount*quote.usdt_per_unit,8)) THEN
            RAISE EXCEPTION 'Journal rate does not match amount or currency' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER journal_precision BEFORE INSERT OR UPDATE ON journal_lines
    FOR EACH ROW EXECUTE FUNCTION check_journal_precision_and_rate();

CREATE FUNCTION check_inventory_posting() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM journal_lines l JOIN cash_accounts c ON c.ledger_account_id=l.account_id
        LEFT JOIN inventory_movements m ON m.operation_id=l.operation_id AND m.line_number=l.line_number
        WHERE l.operation_id=NEW.id AND (m.operation_id IS NULL OR m.instrument_code<>c.instrument_code
            OR m.quantity_delta<>CASE l.side WHEN 'debit' THEN l.native_amount ELSE -l.native_amount END
            OR m.cost_delta_usdt<>CASE l.side WHEN 'debit' THEN l.amount_usdt ELSE -l.amount_usdt END)) THEN
        RAISE EXCEPTION 'Cash journal requires matching inventory movements' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM inventory_movements m JOIN journal_lines l USING(operation_id,line_number)
        LEFT JOIN cash_accounts c ON c.ledger_account_id=l.account_id
        WHERE m.operation_id=NEW.id AND (c.instrument_code IS NULL OR c.instrument_code<>m.instrument_code)) THEN
        RAISE EXCEPTION 'Inventory movement must refer to a cash line' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM (SELECT DISTINCT ON(instrument_code) * FROM inventory_movements
            WHERE operation_id=NEW.id ORDER BY instrument_code,line_number DESC) m
        JOIN cash_accounts c ON c.instrument_code=m.instrument_code
        JOIN posted_account_balances b ON b.account_id=c.ledger_account_id
        JOIN inventory_balances i ON i.instrument_code=m.instrument_code
        WHERE m.quantity_after<>b.balance OR m.cost_after_usdt<>b.carrying_usdt
            OR i.quantity<>b.balance OR i.cost_usdt<>b.carrying_usdt) THEN
        RAISE EXCEPTION 'Inventory projection does not match the posted journal' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER zz_inventory_consistency AFTER INSERT OR UPDATE ON financial_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_inventory_posting();

CREATE FUNCTION operation_has_business_document(target BIGINT) RETURNS BOOLEAN
LANGUAGE SQL SET search_path FROM CURRENT AS $$
    SELECT EXISTS(SELECT 1 FROM capital_contributions WHERE operation_id=target)
        OR EXISTS(SELECT 1 FROM capital_withdrawals WHERE operation_id=target)
        OR EXISTS(SELECT 1 FROM investor_interest_accruals WHERE operation_id=target)
        OR EXISTS(SELECT 1 FROM distribution_payments WHERE operation_id=target)
        OR EXISTS(SELECT 1 FROM expenses WHERE operation_id=target)
        OR EXISTS(SELECT 1 FROM expense_resolutions WHERE operation_id=target)
        OR EXISTS(SELECT 1 FROM expense_period_closures WHERE operation_id=target)
        OR EXISTS(SELECT 1 FROM deals WHERE operation_id=target)
        OR EXISTS(SELECT 1 FROM best_change_period_transfers WHERE operation_id=target)
        OR EXISTS(SELECT 1 FROM profit_period_closures WHERE operation_id=target)
$$;
INSERT INTO role_permissions(role,permission)
    SELECT role,permission FROM unnest(ARRAY['manager','accountant','owner','admin']) role
    CROSS JOIN unnest(ARRAY['finance.post','finance.reverse','finance.read']) permission;
INSERT INTO role_permissions(role,permission) VALUES
    ('cashier','finance.read'),('owner','finance.rebuild'),('admin','finance.rebuild');

CREATE OR REPLACE FUNCTION document_operation_id(table_name TEXT, document JSONB) RETURNS BIGINT
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
DECLARE result BIGINT;
BEGIN
    IF document ? 'dependent_operation_id' THEN RETURN (document->>'dependent_operation_id')::bigint; END IF;
    IF document ? 'operation_id' THEN RETURN (document->>'operation_id')::bigint; END IF;
    CASE table_name
        WHEN 'capital_withdrawal_allocations' THEN
            SELECT operation_id INTO result FROM capital_withdrawals WHERE id=(document->>'withdrawal_id')::bigint;
        WHEN 'investor_interest_payment_allocations','partner_profit_payment_allocations' THEN
            SELECT operation_id INTO result FROM distribution_payments WHERE id=(document->>'payment_id')::bigint;
        WHEN 'expense_share_inputs','expense_allocations','expense_obligations' THEN
            SELECT operation_id INTO result FROM expenses WHERE id=(document->>'expense_id')::bigint;
        WHEN 'expense_resolution_allocations' THEN
            SELECT operation_id INTO result FROM expense_resolutions WHERE id=(document->>'resolution_id')::bigint;
        WHEN 'expense_statement_rows' THEN
            SELECT operation_id INTO result FROM expense_period_closures WHERE id=(document->>'closure_id')::bigint;
        WHEN 'deal_legs','best_change_calculations' THEN
            SELECT operation_id INTO result FROM deals WHERE id=(document->>'deal_id')::bigint;
        WHEN 'best_change_transfer_allocations' THEN
            SELECT operation_id INTO result FROM best_change_period_transfers WHERE id=(document->>'transfer_id')::bigint;
        WHEN 'partner_profit_accruals' THEN
            SELECT operation_id INTO result FROM profit_period_closures WHERE id=(document->>'closure_id')::bigint;
        ELSE RAISE EXCEPTION 'Unsupported financial document %',table_name;
    END CASE;
    RETURN result;
END $$;
CREATE TRIGGER protect_dependency BEFORE INSERT OR UPDATE OR DELETE ON financial_operation_dependencies
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();

ALTER TABLE capital_agreements ADD COLUMN created_operation_id BIGINT UNIQUE REFERENCES financial_operations(id);
ALTER TABLE capital_interest_terms ADD COLUMN change_operation_id BIGINT UNIQUE REFERENCES financial_operations(id);
ALTER TABLE investor_interest_accruals ADD COLUMN monthly_interest_percent percentage;
UPDATE investor_interest_accruals a SET monthly_interest_percent=t.monthly_interest_percent
    FROM capital_interest_terms t WHERE t.id=a.term_id;
ALTER TABLE investor_interest_accruals ALTER COLUMN monthly_interest_percent SET NOT NULL;
ALTER TABLE investor_interest_accruals ADD COLUMN decision_reason TEXT;
CREATE TABLE capital_interest_accounts (
    agreement_id BIGINT PRIMARY KEY REFERENCES capital_agreements(id),
    ledger_account_id BIGINT UNIQUE NOT NULL REFERENCES ledger_accounts(id)
);
CREATE TRIGGER immutable_capital_binding BEFORE UPDATE OR DELETE ON capital_principal_accounts
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
CREATE TRIGGER immutable_interest_binding BEFORE UPDATE OR DELETE ON capital_interest_accounts
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
CREATE FUNCTION protect_agreement_identity() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.investor_party_id<>OLD.investor_party_id OR NEW.principal_kind<>OLD.principal_kind
        OR NEW.opened_on<>OLD.opened_on OR NEW.created_operation_id IS DISTINCT FROM OLD.created_operation_id THEN
        RAISE EXCEPTION 'Capital agreement identity is immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER capital_identity BEFORE UPDATE ON capital_agreements
    FOR EACH ROW EXECUTE FUNCTION protect_agreement_identity();
CREATE FUNCTION check_owner_interest() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.monthly_interest_percent<>0 AND EXISTS(SELECT 1 FROM capital_agreements
        WHERE id=NEW.agreement_id AND principal_kind='owner_equity') THEN
        RAISE EXCEPTION 'Owner capital interest must be zero' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER zz_owner_interest BEFORE INSERT OR UPDATE ON capital_interest_terms
    FOR EACH ROW EXECUTE FUNCTION check_owner_interest();
CREATE TABLE capital_decisions (
    operation_id BIGINT PRIMARY KEY REFERENCES financial_operations(id),
    agreement_id BIGINT NOT NULL REFERENCES capital_agreements(id),
    reason TEXT NOT NULL CHECK (btrim(reason)<>'')
);
CREATE TRIGGER immutable_capital_decision BEFORE INSERT OR UPDATE OR DELETE ON capital_decisions
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE VIEW active_financial_originals AS
    SELECT o.* FROM financial_operations o WHERE o.status='posted' AND o.reversal_of_id IS NULL
    AND NOT EXISTS(SELECT 1 FROM financial_operations r WHERE r.reversal_of_id=o.id AND r.status='posted');
CREATE VIEW capital_lot_balances AS
    SELECT c.*,o.business_date,o.effective_at,
        c.quantity-coalesce(a.quantity,0) AS remaining_quantity,
        c.principal_usdt-coalesce(a.principal,0) AS remaining_principal
    FROM capital_contributions c JOIN active_financial_originals o ON o.id=c.operation_id
    LEFT JOIN LATERAL (
        SELECT sum(a.source_quantity) AS quantity,sum(a.principal_usdt) AS principal
        FROM capital_withdrawal_allocations a JOIN capital_withdrawals w ON w.id=a.withdrawal_id
        JOIN active_financial_originals wo ON wo.id=w.operation_id WHERE a.contribution_id=c.id
    ) a ON TRUE;
CREATE VIEW investor_interest_balances AS
    SELECT a.*,o.business_date,a.final_usdt-coalesce(p.paid,0) AS remaining_usdt,
        coalesce(p.paid,0) AS paid_usdt
    FROM investor_interest_accruals a JOIN active_financial_originals o ON o.id=a.operation_id
    LEFT JOIN LATERAL (
        SELECT sum(x.amount_usdt) AS paid FROM investor_interest_payment_allocations x
        JOIN distribution_payments d ON d.id=x.payment_id
        JOIN active_financial_originals po ON po.id=d.operation_id WHERE x.accrual_id=a.id
    ) p ON TRUE;

CREATE FUNCTION provision_capital_accounts(agreement BIGINT) RETURNS VOID LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE ag capital_agreements%ROWTYPE; cur RECORD; account BIGINT;
BEGIN
    SELECT * INTO ag FROM capital_agreements WHERE id=agreement FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Agreement not found' USING ERRCODE='23514'; END IF;
    FOR cur IN SELECT code FROM currencies ORDER BY code LOOP
        INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,party_id,currency_code)
            VALUES ('capital:'||agreement||':'||cur.code,
                CASE ag.principal_kind WHEN 'owner_equity' THEN 'equity' ELSE 'liability' END,
                'capital_principal','credit',ag.investor_party_id,cur.code) RETURNING id INTO account;
        INSERT INTO capital_principal_accounts VALUES (agreement,cur.code,account);
        INSERT INTO account_balances(account_id) VALUES (account);
    END LOOP;
    INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,party_id,currency_code)
        VALUES ('interest:'||agreement,'liability','investor_interest_payable','credit',ag.investor_party_id,'USDT')
        RETURNING id INTO account;
    INSERT INTO capital_interest_accounts VALUES (agreement,account);
    INSERT INTO account_balances(account_id) VALUES (account);
END $$;
SELECT provision_capital_accounts(id) FROM capital_agreements ORDER BY id;

CREATE FUNCTION check_capital_allocations() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM capital_lot_balances WHERE remaining_quantity<0 OR remaining_principal<0
        OR (remaining_quantity=0 AND remaining_principal<>0)) THEN
        RAISE EXCEPTION 'Capital lot overallocated' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM investor_interest_balances WHERE remaining_usdt<0) THEN
        RAISE EXCEPTION 'Interest overpaid' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM investor_interest_balances GROUP BY agreement_id,period HAVING count(*)>1) THEN
        RAISE EXCEPTION 'Interest already accrued for period' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM investor_interest_accruals a JOIN capital_agreements ag ON ag.id=a.agreement_id
        JOIN capital_interest_terms t ON t.id=a.term_id WHERE a.operation_id=NEW.id AND (
            (ag.principal_kind='owner_equity' AND a.final_usdt<>0)
            OR a.monthly_interest_percent<>t.monthly_interest_percent
            OR a.calculated_usdt<>round(a.basis_usdt*a.monthly_interest_percent/100,8)
            OR btrim(coalesce(a.decision_reason,''))='')) THEN
        RAISE EXCEPTION 'Invalid investor interest decision or snapshot' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM capital_withdrawals w JOIN active_financial_originals o ON o.id=w.operation_id
        WHERE w.operation_id=NEW.id AND (
            w.payout_quantity<>(SELECT coalesce(sum(source_quantity),0) FROM capital_withdrawal_allocations WHERE withdrawal_id=w.id)
            OR w.principal_usdt<>(SELECT coalesce(sum(principal_usdt),0) FROM capital_withdrawal_allocations WHERE withdrawal_id=w.id))) THEN
        RAISE EXCEPTION 'Invalid FIFO allocation totals' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM capital_withdrawals w JOIN active_financial_originals o ON o.id=w.operation_id
        JOIN capital_withdrawal_allocations a ON a.withdrawal_id=w.id
        JOIN capital_contributions c ON c.id=a.contribution_id
        WHERE w.operation_id=NEW.id AND (
            NOT EXISTS(SELECT 1 FROM active_financial_originals source WHERE source.id=c.operation_id)
            OR NOT (c.instrument_code=w.payout_instrument_code OR
                (c.instrument_code IN ('THB_CASH','THB_BANK') AND w.payout_instrument_code IN ('THB_CASH','THB_BANK'))))) THEN
        RAISE EXCEPTION 'FIFO must use active source lots in the original currency form' USING ERRCODE='23514';
    END IF;
    IF NEW.kind='interest_payment' AND EXISTS(SELECT 1 FROM distribution_payments p
        WHERE p.operation_id=NEW.id AND (
            p.discharged_usdt<>(SELECT coalesce(sum(amount_usdt),0) FROM investor_interest_payment_allocations WHERE payment_id=p.id)
            OR EXISTS(SELECT 1 FROM investor_interest_payment_allocations x
                JOIN investor_interest_accruals a ON a.id=x.accrual_id
                JOIN capital_agreements ag ON ag.id=a.agreement_id WHERE x.payment_id=p.id AND
                    (ag.investor_party_id<>p.recipient_party_id OR NOT EXISTS(
                        SELECT 1 FROM active_financial_originals source WHERE source.id=a.operation_id))))) THEN
        RAISE EXCEPTION 'Invalid investor interest payment allocations' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER capital_allocation_check AFTER INSERT OR UPDATE ON financial_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_capital_allocations();

ALTER TABLE client_groups ADD CONSTRAINT nonempty_group_name CHECK (btrim(name)<>'');
ALTER TABLE client_groups ADD CONSTRAINT valid_group_code CHECK (code ~ '^[a-z][a-z0-9_]{0,39}$');
CREATE TABLE client_directory_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE CHECK (btrim(idempotency_key)<>'' AND length(idempotency_key)<=200),
    fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    kind TEXT NOT NULL CHECK (kind IN ('group_create','group_rename','client_create','client_group')),
    actor_id BIGINT NOT NULL REFERENCES users(id),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
    result JSONB NOT NULL CHECK (jsonb_typeof(result)='object'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER immutable_directory_event BEFORE UPDATE OR DELETE ON client_directory_events
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
CREATE FUNCTION protect_group_code() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.code<>OLD.code OR NEW.exposes_in_loans<>OLD.exposes_in_loans THEN
        RAISE EXCEPTION 'Client group code is immutable' USING ERRCODE='23514';
    END IF;
    IF btrim(NEW.name)='' THEN RAISE EXCEPTION 'Group name required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stable_client_group BEFORE UPDATE ON client_groups
    FOR EACH ROW EXECUTE FUNCTION protect_group_code();
CREATE FUNCTION protect_client_party() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.party_id<>OLD.party_id THEN
        RAISE EXCEPTION 'Client party identity is immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stable_client_party BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION protect_client_party();
CREATE OR REPLACE VIEW partner_loan_balances AS
    SELECT c.id AS client_id,c.party_id,w.instrument_code,b.currency_code,
        b.balance,b.carrying_usdt,w.ledger_account_id
    FROM clients c JOIN client_groups g ON g.code=c.group_code AND g.code='partner' AND g.exposes_in_loans
    JOIN client_wallet_accounts w ON w.client_id=c.id AND w.kind='client'
    JOIN posted_account_balances b ON b.account_id=w.ledger_account_id WHERE c.is_active;
INSERT INTO role_permissions(role,permission)
    SELECT role,'client.manage' FROM unnest(ARRAY['manager','accountant','owner','admin']) role;

ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch'));
CREATE TABLE market_quotes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_code TEXT NOT NULL REFERENCES instruments(code),
    rate_snapshot_id BIGINT NOT NULL REFERENCES fx_rate_snapshots(id),
    operation_id BIGINT UNIQUE NOT NULL REFERENCES financial_operations(id)
);
CREATE TRIGGER immutable_market_quote BEFORE INSERT OR UPDATE OR DELETE ON market_quotes
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE INDEX quotes_by_instrument ON market_quotes(instrument_code,id DESC);
CREATE TABLE deal_reversals (
    deal_id BIGINT UNIQUE NOT NULL REFERENCES deals(id),
    operation_id BIGINT UNIQUE NOT NULL REFERENCES financial_operations(id),
    reason TEXT NOT NULL CHECK (btrim(reason)<>''),
    replacement_deal_id BIGINT REFERENCES deals(id),
    PRIMARY KEY(deal_id,operation_id)
);
CREATE TRIGGER immutable_deal_reversal BEFORE INSERT OR UPDATE OR DELETE ON deal_reversals
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE VIEW effective_deals AS
    SELECT d.*,CASE WHEN r.operation_id IS NOT NULL THEN 'cancelled' ELSE d.status END AS effective_status,
        r.operation_id AS reversal_operation_id,r.replacement_deal_id
    FROM deals d LEFT JOIN deal_reversals r ON r.deal_id=d.id
    JOIN financial_operations ro ON ro.id=r.operation_id AND ro.status='posted'
    UNION ALL
    SELECT d.*,d.status,NULL::bigint,NULL::bigint FROM deals d
    WHERE NOT EXISTS(SELECT 1 FROM deal_reversals r JOIN financial_operations o ON o.id=r.operation_id
        WHERE r.deal_id=d.id AND o.status='posted');
INSERT INTO role_permissions(role,permission)
    SELECT role,'rates.manage' FROM unnest(ARRAY['manager','accountant','owner','admin']) role;
CREATE FUNCTION check_trade_posting() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM deals d WHERE d.operation_id=NEW.id AND (d.status<>'posted'
        OR (SELECT count(*) FROM deal_legs WHERE deal_id=d.id)<>2)) THEN
        RAISE EXCEPTION 'Posted trade needs both contract legs' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER trade_posting_check AFTER INSERT OR UPDATE ON financial_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_trade_posting();
CREATE TRIGGER immutable_confirmed_transfer BEFORE UPDATE OR DELETE ON payment_watch_events
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
CREATE TABLE telegram_deliveries (
    event_key TEXT NOT NULL REFERENCES telegram_outbox(event_key),
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(event_key,chat_id)
);

INSERT INTO expense_categories(name) VALUES ('Аренда'),('Зарплата'),('Комиссии'),('Прочее');
-- Half of a six-decimal common share may require seven decimal places.
ALTER TABLE expense_allocations ALTER COLUMN percent TYPE NUMERIC(10,7);
ALTER TABLE expense_allocations ADD CONSTRAINT allocation_percent_range CHECK (percent BETWEEN 0 AND 100);
ALTER TABLE expense_obligations DROP CONSTRAINT expense_obligations_original_usdt_check;
ALTER TABLE expense_obligations ADD CHECK (original_usdt>=0);
ALTER TABLE expense_resolution_allocations ADD COLUMN amount_usdt usdt_amount NOT NULL CHECK (amount_usdt>=0);
ALTER TABLE expense_statement_rows DROP CONSTRAINT expense_statement_rows_check;
ALTER TABLE expense_statement_rows ADD COLUMN corrected quantity NOT NULL DEFAULT 0;
ALTER TABLE expense_statement_rows ADD CHECK (closing=opening+accrued-settled-netted+corrected);
INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code,party_id)
    SELECT 'expense-receivable:'||p.party_id||':'||c.code,'asset','expense_receivable','debit',c.code,p.party_id
    FROM business_participants p CROSS JOIN currencies c WHERE p.code='daniyar';
INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code,party_id)
    SELECT 'expense-funding:'||party_id,'equity','personal_expense_funding','credit','USDT',party_id
    FROM business_participants WHERE code IN ('pe','skyex');
INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code,party_id)
    SELECT 'expense-payable:'||party_id,'liability','personal_expense_payable','credit','USDT',party_id
    FROM business_participants WHERE code='daniyar';
INSERT INTO account_balances(account_id)
    SELECT id FROM ledger_accounts WHERE purpose IN ('expense_receivable','personal_expense_funding','personal_expense_payable');
CREATE VIEW expense_obligation_balances AS
    SELECT b.*,o.id AS source_operation_id,o.business_date,
        b.amount-coalesce(r.amount,0) AS remaining,b.original_usdt-coalesce(r.usdt,0) AS remaining_usdt
    FROM expense_obligations b JOIN expenses e ON e.id=b.expense_id
        JOIN active_financial_originals o ON o.id=e.operation_id
    LEFT JOIN LATERAL (
        SELECT sum(a.amount) amount,sum(a.amount_usdt) usdt FROM expense_resolution_allocations a
            JOIN expense_resolutions r ON r.id=a.resolution_id
            JOIN active_financial_originals ro ON ro.id=r.operation_id WHERE a.obligation_id=b.id
    ) r ON TRUE;
CREATE VIEW expense_debt_events AS
    SELECT b.debtor_party_id,b.creditor_party_id,b.currency_code,b.amount AS delta,'accrued'::text AS kind,o.business_date,o.id AS operation_id
        FROM expense_obligations b JOIN expenses e ON e.id=b.expense_id JOIN financial_operations o ON o.id=e.operation_id AND o.status='posted'
    UNION ALL
    SELECT b.debtor_party_id,b.creditor_party_id,b.currency_code,-b.amount,'corrected',r.business_date,r.id
        FROM expense_obligations b JOIN expenses e ON e.id=b.expense_id JOIN financial_operations r ON r.reversal_of_id=e.operation_id AND r.status='posted'
    UNION ALL
    SELECT b.debtor_party_id,b.creditor_party_id,b.currency_code,-a.amount,
        CASE WHEN x.kind='netting' THEN 'netted' ELSE 'settled' END,o.business_date,o.id
        FROM expense_resolution_allocations a JOIN expense_obligations b ON b.id=a.obligation_id
        JOIN expense_resolutions x ON x.id=a.resolution_id JOIN financial_operations o ON o.id=x.operation_id AND o.status='posted'
    UNION ALL
    SELECT b.debtor_party_id,b.creditor_party_id,b.currency_code,a.amount,'corrected',r.business_date,r.id
        FROM expense_resolution_allocations a JOIN expense_obligations b ON b.id=a.obligation_id
        JOIN expense_resolutions x ON x.id=a.resolution_id JOIN financial_operations r ON r.reversal_of_id=x.operation_id AND r.status='posted';
CREATE TABLE expense_period_revisions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period DATE NOT NULL CHECK (extract(day FROM period)=1),
    operation_id BIGINT UNIQUE NOT NULL REFERENCES financial_operations(id),
    supersedes_id BIGINT UNIQUE NOT NULL REFERENCES expense_period_closures(id),
    reason TEXT NOT NULL CHECK (btrim(reason)<>'')
);
CREATE TABLE expense_revision_completions (
    revision_id BIGINT PRIMARY KEY REFERENCES expense_period_revisions(id),
    operation_id BIGINT UNIQUE NOT NULL REFERENCES financial_operations(id)
);
CREATE TRIGGER immutable_expense_revision BEFORE INSERT OR UPDATE OR DELETE ON expense_period_revisions
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE TRIGGER immutable_expense_revision_completion BEFORE INSERT OR UPDATE OR DELETE ON expense_revision_completions
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE FUNCTION expense_revision_allowed(op financial_operations, target DATE) RETURNS BOOLEAN LANGUAGE sql STABLE SET search_path FROM CURRENT AS $$
    SELECT op.context='expenses' AND op.command_details->>'expense_revision_id' IS NOT NULL
        AND EXISTS(SELECT 1 FROM expense_period_revisions r JOIN active_financial_originals origin ON origin.id=r.operation_id
            WHERE r.id::text=op.command_details->>'expense_revision_id' AND r.period=target
                AND NOT EXISTS(SELECT 1 FROM expense_revision_completions c WHERE c.revision_id=r.id AND c.operation_id<>op.id))
        AND EXISTS(SELECT 1 FROM users u JOIN user_roles ur ON ur.user_id=u.id
            JOIN role_permissions p ON p.role=ur.role WHERE u.id=op.created_by AND u.is_active AND p.permission='period.revise')
$$;
CREATE OR REPLACE FUNCTION financial_write_barrier() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.context='expenses' THEN PERFORM pg_advisory_xact_lock(740810,7); END IF;
    PERFORM pg_advisory_xact_lock_shared(740810,3);
    IF NEW.status='posted' AND EXISTS(SELECT 1 FROM period_locks p
        WHERE p.context IN ('ledger',NEW.context) AND p.period=date_trunc('month',NEW.business_date)::date
            AND p.closed_by_operation_id IS DISTINCT FROM NEW.id
            AND NOT (p.context='expenses' AND expense_revision_allowed(NEW,p.period))) THEN
        RAISE EXCEPTION 'Financial period is closed' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION check_expense_documents() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM expenses e WHERE e.operation_id=NEW.id AND
        ((SELECT sum(percent) FROM expense_share_inputs WHERE expense_id=e.id) IS DISTINCT FROM 100::numeric
        OR (SELECT sum(amount) FROM expense_allocations WHERE expense_id=e.id) IS DISTINCT FROM e.amount
        OR (SELECT sum(amount_usdt) FROM expense_allocations WHERE expense_id=e.id) IS DISTINCT FROM e.amount_usdt
        OR (SELECT sum(percent) FROM expense_allocations WHERE expense_id=e.id) IS DISTINCT FROM 100::numeric)) THEN
        RAISE EXCEPTION 'Expense allocation is not balanced' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM expense_resolutions r WHERE r.operation_id=NEW.id AND
        ((SELECT sum(a.amount) FROM expense_resolution_allocations a WHERE a.resolution_id=r.id AND direction='forward') IS DISTINCT FROM r.amount
        OR (r.kind='netting' AND (SELECT sum(a.amount) FROM expense_resolution_allocations a WHERE a.resolution_id=r.id AND direction='reverse') IS DISTINCT FROM r.amount)
        OR EXISTS(SELECT 1 FROM expense_resolution_allocations a JOIN expense_obligations b ON b.id=a.obligation_id
            WHERE a.resolution_id=r.id AND (b.currency_code<>r.currency_code
                OR (a.direction='forward' AND (b.debtor_party_id<>r.payer_party_id OR b.creditor_party_id<>r.recipient_party_id))
                OR (a.direction='reverse' AND (r.kind<>'netting' OR b.debtor_party_id<>r.recipient_party_id OR b.creditor_party_id<>r.payer_party_id)))))) THEN
        RAISE EXCEPTION 'Expense resolution must allocate its full amount to matching claims' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM expense_resolution_allocations a JOIN expense_resolutions r ON r.id=a.resolution_id
        JOIN active_financial_originals ro ON ro.id=r.operation_id JOIN expense_obligations b ON b.id=a.obligation_id
        JOIN expenses e ON e.id=b.expense_id LEFT JOIN active_financial_originals eo ON eo.id=e.operation_id WHERE eo.id IS NULL) THEN
        RAISE EXCEPTION 'Reverse dependent resolutions before the source expense' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM expense_obligation_balances WHERE remaining<0 OR remaining_usdt<0) THEN
        RAISE EXCEPTION 'Expense obligation is overpaid' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER expense_document_check AFTER INSERT OR UPDATE ON financial_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_expense_documents();
CREATE TABLE expense_corrections (
    original_expense_id BIGINT PRIMARY KEY REFERENCES expenses(id),
    replacement_expense_id BIGINT UNIQUE NOT NULL REFERENCES expenses(id),
    operation_id BIGINT UNIQUE NOT NULL REFERENCES financial_operations(id)
);
CREATE TRIGGER immutable_expense_correction BEFORE INSERT OR UPDATE OR DELETE ON expense_corrections
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();

ALTER TABLE client_wallet_accounts DROP CONSTRAINT client_wallet_accounts_pkey;
ALTER TABLE client_wallet_accounts ADD PRIMARY KEY(client_id,instrument_code,kind);
INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code,party_id)
    SELECT 'owner-profit:'||party_id,'liability','owner_profit_payable','credit','USDT',party_id
        FROM business_participants WHERE code IN ('pe','skyex');
DO $$ DECLARE vendor BIGINT;
BEGIN
    INSERT INTO parties(name,kind) VALUES ('BestChange: комиссия платформы','organization') RETURNING id INTO vendor;
    INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code,party_id)
        VALUES ('platform-commission-payable','liability','platform_commission_payable','credit','USDT',vendor);
END $$;
INSERT INTO account_balances(account_id) SELECT id FROM ledger_accounts WHERE purpose IN ('owner_profit_payable','platform_commission_payable');
ALTER TABLE profit_share_terms ADD COLUMN operation_id BIGINT UNIQUE REFERENCES financial_operations(id);
ALTER TABLE profit_share_terms ADD COLUMN supersedes_id BIGINT UNIQUE REFERENCES profit_share_terms(id);
ALTER TABLE profit_share_terms DROP CONSTRAINT profit_share_terms_valid_from_key;
CREATE OR REPLACE FUNCTION check_profit_term_interval() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(740810,2);
    IF NEW.operation_id IS NULL AND EXISTS(SELECT 1 FROM profit_share_terms t WHERE t.id<>NEW.id
        AND daterange(t.valid_from,t.valid_until,'[)') && daterange(NEW.valid_from,NEW.valid_until,'[)')) THEN
        RAISE EXCEPTION 'Profit share intervals overlap' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER immutable_profit_terms BEFORE INSERT OR UPDATE OR DELETE ON profit_share_terms
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
ALTER TABLE profit_period_closures DROP CONSTRAINT profit_period_closures_business_expenses_usdt_check;
ALTER TABLE profit_period_closures DROP CONSTRAINT profit_period_closures_investor_interest_usdt_check;
CREATE TABLE profit_closure_sources (
    closure_id BIGINT NOT NULL REFERENCES profit_period_closures(id),
    operation_id BIGINT NOT NULL REFERENCES financial_operations(id),
    source_operation_id BIGINT NOT NULL REFERENCES financial_operations(id),
    account_id BIGINT NOT NULL REFERENCES ledger_accounts(id),
    credit_signed_usdt usdt_amount NOT NULL,
    PRIMARY KEY(closure_id,source_operation_id,account_id)
);
CREATE TRIGGER immutable_profit_sources BEFORE INSERT OR UPDATE OR DELETE ON profit_closure_sources
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE TABLE platform_payment_allocations (
    payment_id BIGINT NOT NULL REFERENCES distribution_payments(id),
    deal_id BIGINT NOT NULL REFERENCES best_change_calculations(deal_id),
    operation_id BIGINT NOT NULL REFERENCES financial_operations(id),
    amount_usdt usdt_amount NOT NULL CHECK (amount_usdt>0),
    PRIMARY KEY(payment_id,deal_id)
);
CREATE TRIGGER immutable_platform_allocations BEFORE INSERT OR UPDATE OR DELETE ON platform_payment_allocations
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE VIEW owner_profit_balances AS
    SELECT a.*,o.id AS operation_id,c.period,
        a.amount_usdt-coalesce(p.amount,0) AS remaining_usdt
    FROM partner_profit_accruals a JOIN profit_period_closures c ON c.id=a.closure_id
        JOIN active_financial_originals o ON o.id=c.operation_id
    LEFT JOIN LATERAL (SELECT sum(x.amount_usdt) AS amount FROM partner_profit_payment_allocations x
        JOIN distribution_payments p ON p.id=x.payment_id JOIN active_financial_originals po ON po.id=p.operation_id
        WHERE x.accrual_id=a.id) p ON TRUE;
CREATE VIEW platform_commission_balances AS
    SELECT c.*,d.operation_id,o.business_date,c.deal_id AS id,
        c.platform_fee_usdt-coalesce(p.amount,0) AS remaining_usdt
    FROM best_change_calculations c JOIN deals d ON d.id=c.deal_id JOIN active_financial_originals o ON o.id=d.operation_id
    LEFT JOIN LATERAL (SELECT sum(a.amount_usdt) AS amount FROM platform_payment_allocations a
        JOIN active_financial_originals po ON po.id=a.operation_id WHERE a.deal_id=c.deal_id) p ON TRUE;
CREATE TABLE profit_period_revisions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period DATE NOT NULL CHECK (extract(day FROM period)=1),
    operation_id BIGINT UNIQUE NOT NULL REFERENCES financial_operations(id),
    supersedes_id BIGINT UNIQUE NOT NULL REFERENCES profit_period_closures(id),
    reason TEXT NOT NULL CHECK (btrim(reason)<>'')
);
CREATE TABLE profit_revision_completions (
    revision_id BIGINT PRIMARY KEY REFERENCES profit_period_revisions(id),
    operation_id BIGINT UNIQUE NOT NULL REFERENCES financial_operations(id)
);
CREATE TRIGGER immutable_profit_revision BEFORE INSERT OR UPDATE OR DELETE ON profit_period_revisions
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE TRIGGER immutable_profit_revision_completion BEFORE INSERT OR UPDATE OR DELETE ON profit_revision_completions
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE TABLE best_change_corrections (
    original_deal_id BIGINT PRIMARY KEY REFERENCES best_change_calculations(deal_id),
    replacement_deal_id BIGINT UNIQUE NOT NULL REFERENCES best_change_calculations(deal_id),
    operation_id BIGINT UNIQUE NOT NULL REFERENCES financial_operations(id)
);
CREATE TRIGGER immutable_best_change_correction BEFORE INSERT OR UPDATE OR DELETE ON best_change_corrections
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE FUNCTION profit_revision_allowed(op financial_operations,target DATE) RETURNS BOOLEAN LANGUAGE sql STABLE SET search_path FROM CURRENT AS $$
    SELECT EXISTS(SELECT 1 FROM profit_period_revisions r JOIN active_financial_originals origin ON origin.id=r.operation_id
        WHERE r.id::text=op.command_details->>'profit_revision_id' AND r.period=target
            AND NOT EXISTS(SELECT 1 FROM profit_revision_completions c WHERE c.revision_id=r.id AND c.operation_id<>op.id))
        AND EXISTS(SELECT 1 FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN role_permissions p ON p.role=ur.role
            WHERE u.id=op.created_by AND u.is_active AND p.permission='period.revise')
$$;
CREATE OR REPLACE FUNCTION financial_write_barrier() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.context='expenses' THEN PERFORM pg_advisory_xact_lock(740810,7); END IF;
    PERFORM pg_advisory_xact_lock_shared(740810,3);
    IF NEW.status='posted' AND EXISTS(SELECT 1 FROM period_locks p
        WHERE p.context IN ('ledger',NEW.context) AND p.period=date_trunc('month',NEW.business_date)::date
            AND p.closed_by_operation_id IS DISTINCT FROM NEW.id
            AND NOT (p.context='expenses' AND expense_revision_allowed(NEW,p.period))
            AND NOT (p.context IN ('ledger','profit') AND profit_revision_allowed(NEW,p.period))
            AND NOT (NEW.kind='profit_revision' AND NEW.posting_mode='memorandum'
                AND EXISTS(SELECT 1 FROM profit_period_revisions r WHERE r.operation_id=NEW.id)
                AND EXISTS(SELECT 1 FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN role_permissions rp ON rp.role=ur.role
                    WHERE u.id=NEW.created_by AND u.is_active AND rp.permission='period.revise'))) THEN
        RAISE EXCEPTION 'Financial period is closed' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION check_profit_documents() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM owner_profit_balances WHERE remaining_usdt<0)
        OR EXISTS(SELECT 1 FROM platform_commission_balances WHERE remaining_usdt<0) THEN
        RAISE EXCEPTION 'Profit or commission obligation overpaid' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM profit_period_closures c WHERE c.operation_id=NEW.id
        AND coalesce((SELECT sum(a.amount_usdt) FROM partner_profit_accruals a WHERE a.closure_id=c.id),0)<>greatest(c.net_profit_usdt,0)) THEN
        RAISE EXCEPTION 'Owner distributions must match positive net profit' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM distribution_payments p JOIN financial_operations o ON o.id=p.operation_id
        WHERE o.id=NEW.id AND (
            (o.kind='profit_payment' AND p.discharged_usdt<>coalesce((SELECT sum(a.amount_usdt) FROM partner_profit_payment_allocations a WHERE a.payment_id=p.id),0)) OR
            (o.kind='platform_payment' AND p.discharged_usdt<>coalesce((SELECT sum(a.amount_usdt) FROM platform_payment_allocations a WHERE a.payment_id=p.id),0)))) THEN
        RAISE EXCEPTION 'Profit payment allocations must match payment' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM partner_profit_payment_allocations a
        JOIN distribution_payments p ON p.id=a.payment_id JOIN active_financial_originals po ON po.id=p.operation_id
        JOIN partner_profit_accruals accr ON accr.id=a.accrual_id JOIN profit_period_closures c ON c.id=accr.closure_id
        WHERE p.recipient_party_id<>accr.owner_party_id OR NOT EXISTS(SELECT 1 FROM active_financial_originals co WHERE co.id=c.operation_id))
        OR EXISTS(SELECT 1 FROM platform_payment_allocations a JOIN active_financial_originals po ON po.id=a.operation_id
            JOIN deals d ON d.id=a.deal_id WHERE NOT EXISTS(SELECT 1 FROM active_financial_originals co WHERE co.id=d.operation_id)) THEN
        RAISE EXCEPTION 'Active payout requires active matching accrual' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM best_change_period_transfers t WHERE t.operation_id=NEW.id
        AND (t.amount_usdt<>coalesce((SELECT sum(a.amount_usdt) FROM best_change_transfer_allocations a WHERE a.transfer_id=t.id),0)
            OR EXISTS(SELECT 1 FROM best_change_transfer_allocations a JOIN best_change_calculations b ON b.deal_id=a.deal_id
                WHERE a.transfer_id=t.id AND a.amount_usdt<>b.daniyar_share_usdt))) THEN
        RAISE EXCEPTION 'BestChange transfer allocations must match calculation' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER profit_document_check AFTER INSERT OR UPDATE ON financial_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_profit_documents();

-- Telegram customers are not CRM staff. Audit registration without impersonating a user.
CREATE TABLE telegram_client_bootstrap_events (
    idempotency_key TEXT PRIMARY KEY CHECK (btrim(idempotency_key)<>'' AND length(idempotency_key)<=200),
    fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    telegram_user_id BIGINT NOT NULL CHECK (telegram_user_id>0),
    telegram_chat_id BIGINT NOT NULL CHECK (telegram_chat_id<>0),
    client_id BIGINT NOT NULL REFERENCES clients(id),
    name_snapshot TEXT NOT NULL CHECK (btrim(name_snapshot)<>''),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER immutable_telegram_bootstrap BEFORE UPDATE OR DELETE ON telegram_client_bootstrap_events
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();

-- Cash requests use existing deals, message links and outbox; only extend the audit vocabulary.
ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch',
        'cash_request_create','cash_request_edit','cash_request_status'));
CREATE INDEX cash_request_message_links ON telegram_message_links(deal_id,chat_id)
    WHERE purpose IN ('cash_client','cash_request');

CREATE OR REPLACE FUNCTION check_trade_posting() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM deals d WHERE d.operation_id=NEW.id AND (d.status<>'posted'
        OR (SELECT count(*) FROM deal_legs WHERE deal_id=d.id)<>
            CASE WHEN d.kind IN ('deposit','withdrawal') THEN 1 ELSE 2 END)) THEN
        RAISE EXCEPTION 'Posted request needs its contract legs' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM deals d WHERE d.operation_id=NEW.id AND d.kind IN ('deposit','withdrawal')
        AND (NEW.kind<>CASE WHEN d.kind='deposit' THEN 'wallet_deposit' ELSE 'wallet_withdrawal' END
          OR (NEW.command_details->>'request_id')::bigint IS DISTINCT FROM d.id
          OR (NEW.command_details->>'client')::bigint IS DISTINCT FROM d.client_id
          OR NEW.command_details->>'instrument' IS DISTINCT FROM d.attributes->>'instrument'
          OR (NEW.command_details->>'quantity')::numeric IS DISTINCT FROM (d.attributes->>'quantity')::numeric
          OR NOT EXISTS(SELECT 1 FROM deal_legs l WHERE l.deal_id=d.id
              AND l.instrument_code=d.attributes->>'instrument'
              AND l.quantity=(d.attributes->>'quantity')::numeric
              AND l.direction=CASE WHEN d.kind='deposit' THEN 'receive' ELSE 'pay' END))) THEN
        RAISE EXCEPTION 'Cash settlement differs from request contract' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status'));
CREATE INDEX exchange_request_message_links ON telegram_message_links(deal_id,chat_id)
    WHERE purpose IN ('exchange_client','exchange_request');
CREATE UNIQUE INDEX exchange_replacement_parent ON deal_reversals(replacement_deal_id)
    WHERE replacement_deal_id IS NOT NULL;

CREATE TABLE cash_exchange_confirmations (
    deal_id BIGINT NOT NULL REFERENCES deals(id),
    direction TEXT NOT NULL CHECK (direction IN ('receive','pay')),
    instrument_code TEXT NOT NULL REFERENCES instruments(code),
    quantity quantity NOT NULL CHECK (quantity>0),
    quoted_rate NUMERIC CHECK (quoted_rate>0),
    actor_id BIGINT NOT NULL REFERENCES users(id),
    confirmed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(deal_id,direction)
);
CREATE FUNCTION validate_cash_exchange_confirmation() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE request deals%ROWTYPE;
BEGIN
    SELECT * INTO request FROM deals WHERE id=NEW.deal_id FOR UPDATE;
    IF request.id IS NULL OR request.kind<>'cash_exchange' OR request.status<>'ready'
        OR NEW.instrument_code IS DISTINCT FROM request.attributes->NEW.direction->>'instrument'
        OR NEW.quantity IS DISTINCT FROM (request.attributes->NEW.direction->>'quantity')::numeric THEN
        RAISE EXCEPTION 'Confirmation differs from ready cash exchange' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER valid_cash_exchange_confirmation BEFORE INSERT ON cash_exchange_confirmations
    FOR EACH ROW EXECUTE FUNCTION validate_cash_exchange_confirmation();
CREATE TRIGGER immutable_cash_exchange_confirmation BEFORE UPDATE OR DELETE ON cash_exchange_confirmations
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status','cash_request_confirm'));

CREATE OR REPLACE FUNCTION check_trade_posting() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM deals d WHERE d.operation_id=NEW.id AND (d.status<>'posted'
        OR (SELECT count(*) FROM deal_legs WHERE deal_id=d.id)<>
            CASE WHEN d.kind IN ('deposit','withdrawal') THEN 1 ELSE 2 END)) THEN
        RAISE EXCEPTION 'Posted request needs its contract legs' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM deals d WHERE d.operation_id=NEW.id AND d.kind IN ('deposit','withdrawal')
        AND (NEW.kind<>CASE WHEN d.kind='deposit' THEN 'wallet_deposit' ELSE 'wallet_withdrawal' END
          OR (NEW.command_details->>'request_id')::bigint IS DISTINCT FROM d.id
          OR (NEW.command_details->>'client')::bigint IS DISTINCT FROM d.client_id
          OR NEW.command_details->>'instrument' IS DISTINCT FROM d.attributes->>'instrument'
          OR (NEW.command_details->>'quantity')::numeric IS DISTINCT FROM (d.attributes->>'quantity')::numeric
          OR NOT EXISTS(SELECT 1 FROM deal_legs l WHERE l.deal_id=d.id
              AND l.instrument_code=d.attributes->>'instrument'
              AND l.quantity=(d.attributes->>'quantity')::numeric
              AND l.direction=CASE WHEN d.kind='deposit' THEN 'receive' ELSE 'pay' END))) THEN
        RAISE EXCEPTION 'Cash settlement differs from request contract' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM deals d WHERE d.operation_id=NEW.id AND d.kind='cash_exchange'
        AND (NEW.kind<>'fx_trade' OR d.attributes->>'mode'<>'cash'
            OR (NEW.command_details->>'cash_request_id')::bigint IS DISTINCT FROM d.id
            OR (SELECT count(*) FROM cash_exchange_confirmations WHERE deal_id=d.id)<>2
            OR EXISTS(SELECT 1 FROM cash_exchange_confirmations c WHERE c.deal_id=d.id
                AND (c.instrument_code IS DISTINCT FROM d.attributes->c.direction->>'instrument'
                    OR c.quantity IS DISTINCT FROM (d.attributes->c.direction->>'quantity')::numeric))
            OR EXISTS(SELECT 1 FROM deal_legs l WHERE l.deal_id=d.id
                AND (l.instrument_code IS DISTINCT FROM d.attributes->l.direction->>'instrument'
                    OR l.quantity IS DISTINCT FROM (d.attributes->l.direction->>'quantity')::numeric)))) THEN
        RAISE EXCEPTION 'Cash exchange needs both matching confirmations' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status','cash_request_confirm',
        'client_transfer_request','client_transfer_resolve','client_transfer_adjust'));
CREATE INDEX client_transfer_versions ON financial_operations
    (((command_details->>'transfer_root_id')::bigint)) WHERE kind='wallet_transfer';
CREATE UNIQUE INDEX client_transfer_message_delivery ON telegram_message_links(operation_id,chat_id,purpose)
    WHERE purpose IN ('client_transfer_receipt_posted','client_transfer_receipt_cancelled',
        'client_transfer_text_posted','client_transfer_text_cancelled');

ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status','cash_request_confirm',
        'client_transfer_request','client_transfer_resolve','client_transfer_adjust',
        'wallet_undo','wallet_zero_request','wallet_zero_resolve'));
CREATE UNIQUE INDEX wallet_adjustment_message_delivery ON telegram_message_links(operation_id,chat_id)
    WHERE purpose='wallet_adjustment';

ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status','cash_request_confirm',
        'client_transfer_request','client_transfer_resolve','client_transfer_adjust',
        'wallet_undo','wallet_zero_request','wallet_zero_resolve',
        'best_change_correct','payment_watch_start','payment_watch_control','payment_wallet_set'));
ALTER TABLE payment_watches ADD COLUMN client_id BIGINT REFERENCES clients(id);
ALTER TABLE payment_watches ADD COLUMN expected_quantity quantity CHECK(expected_quantity>0);
ALTER TABLE payment_watches ADD COLUMN review_required BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE payment_watches ADD CONSTRAINT standalone_payment_watch
    CHECK ((client_id IS NULL AND expected_quantity IS NULL) OR
        (client_id IS NOT NULL AND expected_quantity IS NOT NULL AND deal_id IS NULL));
CREATE UNIQUE INDEX active_payment_addresses ON payment_watches(our_address,address)
    WHERE status IN ('WATCHING','TIMED_OUT');
CREATE UNIQUE INDEX best_change_message_delivery ON telegram_message_links(deal_id,chat_id)
    WHERE purpose='best_change';
CREATE UNIQUE INDEX payment_receipt_delivery ON telegram_message_links(operation_id,chat_id,purpose)
    WHERE purpose IN ('payment_receipt','payment_balance');
CREATE FUNCTION validate_monitored_payment() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
DECLARE w payment_watches%ROWTYPE; op financial_operations%ROWTYPE;
BEGIN
    SELECT * INTO w FROM payment_watches WHERE id=NEW.watch_id FOR UPDATE;
    IF w.status<>'WATCHING' OR NEW.confirmations<2 OR NEW.token_symbol<>'USDT'
        OR NEW.direction<>'OUT' OR NEW.block_ts<w.started_at THEN
        RAISE EXCEPTION 'Payment needs active watch and two outbound USDT confirmations' USING ERRCODE='23514';
    END IF;
    IF w.client_id IS NOT NULL THEN
        SELECT * INTO op FROM financial_operations WHERE id=NEW.operation_id;
        IF op.kind IS DISTINCT FROM 'wallet_withdrawal'
            OR (op.command_details->>'client')::bigint IS DISTINCT FROM w.client_id
            OR op.command_details->>'instrument' IS DISTINCT FROM 'USDT'
            OR (op.command_details->>'quantity')::numeric IS DISTINCT FROM NEW.amount
            OR (op.command_details->>'payment_watch_id')::bigint IS DISTINCT FROM w.id
            OR NEW.event_type IS DISTINCT FROM w.phase
            OR (NEW.event_type='TEST' AND NEW.amount<>1) THEN
            RAISE EXCEPTION 'Payment evidence differs from wallet withdrawal' USING ERRCODE='23514';
        END IF;
        IF w.mode='TEST_THEN_MAIN' AND NEW.event_type='MAIN' AND NOT EXISTS(
            SELECT 1 FROM payment_watch_events e WHERE e.watch_id=w.id AND e.event_type='TEST'
                AND e.block_ts<=NEW.block_ts) THEN
            RAISE EXCEPTION 'Main transfer must follow confirmed test' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER monitored_payment BEFORE INSERT ON payment_watch_events
    FOR EACH ROW EXECUTE FUNCTION validate_monitored_payment();
CREATE FUNCTION require_monitored_payment() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status='posted' AND NEW.command_details ? 'payment_watch_id' AND NOT EXISTS(
        SELECT 1 FROM payment_watch_events e WHERE e.operation_id=NEW.id
            AND e.watch_id=(NEW.command_details->>'payment_watch_id')::bigint) THEN
        RAISE EXCEPTION 'Monitored withdrawal needs persisted chain evidence' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER monitored_payment_posting AFTER INSERT OR UPDATE ON financial_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_monitored_payment();

-- Compound corrections retain an inventory snapshot for each constituent operation.
CREATE OR REPLACE FUNCTION check_inventory_posting() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM journal_lines l JOIN cash_accounts c ON c.ledger_account_id=l.account_id
        LEFT JOIN inventory_movements m ON m.operation_id=l.operation_id AND m.line_number=l.line_number
        WHERE l.operation_id=NEW.id AND (m.operation_id IS NULL OR m.instrument_code<>c.instrument_code
            OR m.quantity_delta<>CASE l.side WHEN 'debit' THEN l.native_amount ELSE -l.native_amount END
            OR m.cost_delta_usdt<>CASE l.side WHEN 'debit' THEN l.amount_usdt ELSE -l.amount_usdt END)) THEN
        RAISE EXCEPTION 'Cash journal requires matching inventory movements' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM inventory_movements m JOIN journal_lines l USING(operation_id,line_number)
        LEFT JOIN cash_accounts c ON c.ledger_account_id=l.account_id
        WHERE m.operation_id=NEW.id AND (c.instrument_code IS NULL OR c.instrument_code<>m.instrument_code)) THEN
        RAISE EXCEPTION 'Inventory movement must refer to a cash line' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM (SELECT DISTINCT ON(instrument_code) * FROM inventory_movements
            WHERE operation_id=NEW.id ORDER BY instrument_code,line_number DESC) m
        JOIN cash_accounts c ON c.instrument_code=m.instrument_code
        JOIN posted_account_balances b ON b.account_id=c.ledger_account_id
        JOIN inventory_balances i ON i.instrument_code=m.instrument_code
        JOIN LATERAL (
            SELECT coalesce(sum(CASE l.side WHEN 'debit' THEN l.native_amount ELSE -l.native_amount END),0) AS quantity,
                coalesce(sum(CASE l.side WHEN 'debit' THEN l.amount_usdt ELSE -l.amount_usdt END),0) AS cost
            FROM journal_lines l JOIN financial_operations o ON o.id=l.operation_id
            WHERE l.account_id=c.ledger_account_id AND o.status='posted' AND o.id<=NEW.id
        ) historical ON TRUE
        WHERE m.quantity_after<>historical.quantity OR m.cost_after_usdt<>historical.cost
            OR i.quantity<>b.balance OR i.cost_usdt<>b.carrying_usdt) THEN
        RAISE EXCEPTION 'Inventory projection does not match the posted journal' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status','cash_request_confirm',
        'client_transfer_request','client_transfer_resolve','client_transfer_adjust',
        'wallet_undo','wallet_zero_request','wallet_zero_resolve',
        'best_change_correct','payment_watch_start','payment_watch_control','payment_wallet_set','act_adjust'));

CREATE TABLE act_counter_adjustments (
    event_key TEXT PRIMARY KEY REFERENCES client_directory_events(idempotency_key)
        DEFERRABLE INITIALLY DEFERRED,
    request_chat_id BIGINT NOT NULL,
    actor_id BIGINT NOT NULL REFERENCES users(id),
    delta quantity NOT NULL CHECK (delta=round(delta,6)),
    target_amount quantity NOT NULL CHECK (target_amount=round(target_amount,6)),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX act_counter_chat ON act_counter_adjustments(request_chat_id);
CREATE TRIGGER immutable_act_adjustment BEFORE UPDATE OR DELETE ON act_counter_adjustments
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
INSERT INTO role_permissions(role,permission)
    SELECT role,'act.reconcile' FROM unnest(ARRAY['manager','accountant','owner','admin']) role;

ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status','cash_request_confirm',
        'client_transfer_request','client_transfer_resolve','client_transfer_adjust',
        'wallet_undo','wallet_zero_request','wallet_zero_resolve',
        'best_change_correct','payment_watch_start','payment_watch_control','payment_wallet_set',
        'act_adjust','manager_change'));

ALTER TABLE journal_lines ADD COLUMN unvalued_native quantity NOT NULL DEFAULT 0
    CHECK(unvalued_native>=0 AND unvalued_native<=native_amount AND unvalued_native=round(unvalued_native,2)
        AND (unvalued_native=0 OR currency_code='THB')
        AND (unvalued_native=0 OR unvalued_native<native_amount OR amount_usdt=0));
ALTER TABLE journal_lines ADD COLUMN valuation_component BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE account_balances ADD COLUMN debit_signed_unvalued quantity NOT NULL DEFAULT 0;
ALTER TABLE inventory_balances ADD COLUMN unvalued_quantity quantity NOT NULL DEFAULT 0
    CHECK(unvalued_quantity>=0 AND unvalued_quantity<=quantity);
ALTER TABLE inventory_movements ADD COLUMN unvalued_delta quantity NOT NULL DEFAULT 0;
ALTER TABLE inventory_movements ADD COLUMN unvalued_after quantity NOT NULL DEFAULT 0;

CREATE OR REPLACE VIEW posted_account_balances AS
    SELECT a.id AS account_id,a.currency_code,a.instrument_code,
        coalesce(sum(CASE WHEN l.side=a.normal_side THEN l.native_amount ELSE -l.native_amount END),0) AS balance,
        coalesce(sum(CASE WHEN l.side=a.normal_side THEN l.amount_usdt ELSE -l.amount_usdt END),0) AS carrying_usdt,
        coalesce(sum(CASE WHEN l.side=a.normal_side THEN l.unvalued_native ELSE -l.unvalued_native END),0) AS unvalued_native
    FROM ledger_accounts a LEFT JOIN journal_lines l ON l.account_id=a.id
        AND EXISTS(SELECT 1 FROM financial_operations o WHERE o.id=l.operation_id AND o.status='posted')
    GROUP BY a.id;

CREATE OR REPLACE VIEW partner_loan_balances AS
    SELECT c.id AS client_id,c.party_id,w.instrument_code,b.currency_code,
        b.balance,b.carrying_usdt,w.ledger_account_id,b.unvalued_native AS unvalued_quantity
    FROM clients c JOIN client_groups g ON g.code=c.group_code AND g.code='partner' AND g.exposes_in_loans
    JOIN client_wallet_accounts w ON w.client_id=c.id AND w.kind='client'
    JOIN posted_account_balances b ON b.account_id=w.ledger_account_id WHERE c.is_active;

CREATE FUNCTION check_deferred_thb() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id
        WHERE l.operation_id=NEW.id AND l.unvalued_native>0 AND a.purpose NOT IN ('cash','client_wallet')) THEN
        RAISE EXCEPTION 'Deferred funds need cash or client wallet account' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM journal_lines l WHERE l.operation_id=NEW.id AND l.valuation_component
        GROUP BY l.account_id HAVING sum(CASE l.side WHEN 'debit' THEN l.native_amount ELSE -l.native_amount END)<>0) THEN
        RAISE EXCEPTION 'Valuation must preserve native balances' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM posted_account_balances b JOIN ledger_accounts a ON a.id=b.account_id
        WHERE (b.unvalued_native<0 OR b.unvalued_native>greatest(b.balance,0))
            AND a.purpose IN ('cash','client_wallet')) THEN
        RAISE EXCEPTION 'Invalid unvalued THB balance' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM cash_accounts c JOIN posted_account_balances b ON b.account_id=c.ledger_account_id
        JOIN inventory_balances i ON i.instrument_code=c.instrument_code
        WHERE b.unvalued_native<>i.unvalued_quantity OR b.unvalued_native<>
            (SELECT coalesce(sum(wb.unvalued_native),0) FROM client_wallet_accounts w
                JOIN posted_account_balances wb ON wb.account_id=w.ledger_account_id
                WHERE w.instrument_code=c.instrument_code AND w.kind='client')) THEN
        RAISE EXCEPTION 'Cash and client unvalued funds differ' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM account_balances p JOIN posted_account_balances b ON b.account_id=p.account_id
        JOIN ledger_accounts a ON a.id=p.account_id WHERE p.debit_signed_unvalued<>
            CASE a.normal_side WHEN 'debit' THEN b.unvalued_native ELSE -b.unvalued_native END) THEN
        RAISE EXCEPTION 'Unvalued projection differs from journal' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM inventory_movements m JOIN journal_lines l USING(operation_id,line_number)
        JOIN cash_accounts c ON c.ledger_account_id=l.account_id
        WHERE m.operation_id=NEW.id AND (m.unvalued_delta<>
                CASE l.side WHEN 'debit' THEN l.unvalued_native ELSE -l.unvalued_native END
            OR m.unvalued_after<>(SELECT coalesce(sum(CASE jl.side WHEN 'debit' THEN jl.unvalued_native ELSE -jl.unvalued_native END),0)
                FROM journal_lines jl JOIN financial_operations o ON o.id=jl.operation_id
                WHERE jl.account_id=c.ledger_account_id AND o.status='posted'
                    AND (o.id<m.operation_id OR (o.id=m.operation_id AND jl.line_number<=m.line_number))))) THEN
        RAISE EXCEPTION 'Unvalued inventory snapshot differs from journal' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER deferred_thb_guard AFTER INSERT OR UPDATE ON financial_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_deferred_thb();

-- Managers added through /mgr have full CRM editing access.
-- Keep a single permission source for Telegram, OIDC, API and UI.
INSERT INTO role_permissions(role, permission)
SELECT 'manager', permission FROM role_permissions WHERE role = 'admin'
ON CONFLICT DO NOTHING;
INSERT INTO role_permissions(role, permission) VALUES ('manager', 'cash.execute')
ON CONFLICT DO NOTHING;
