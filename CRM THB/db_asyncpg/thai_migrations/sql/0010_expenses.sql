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
