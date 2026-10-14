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
