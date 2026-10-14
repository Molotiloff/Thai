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
