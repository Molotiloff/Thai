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
