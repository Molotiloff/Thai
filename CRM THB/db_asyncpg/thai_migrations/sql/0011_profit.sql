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
