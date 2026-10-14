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
