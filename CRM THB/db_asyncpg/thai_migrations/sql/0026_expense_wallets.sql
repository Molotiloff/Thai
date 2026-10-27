-- Personal expense financing uses ordinary client USDT wallets; capital is separate.
ALTER TABLE business_client_bindings DROP CONSTRAINT business_client_bindings_purpose_check;
ALTER TABLE business_client_bindings ADD CHECK (purpose IN ('daniyar_balance','best_change_buffer','skyex_balance','pe_balance'));
ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status','cash_request_confirm',
        'client_transfer_request','client_transfer_resolve','client_transfer_adjust',
        'wallet_undo','cash_undo','wallet_zero_request','wallet_zero_resolve',
        'best_change_correct','payment_watch_start','payment_watch_control','payment_wallet_set',
        'act_adjust','manager_change','expense_bind'));
ALTER TABLE expenses ADD COLUMN settlement_version SMALLINT NOT NULL DEFAULT 1 CHECK (settlement_version IN (1,2));
ALTER TABLE expenses ADD UNIQUE(id,operation_id);
CREATE TABLE expense_wallet_entries (
    expense_id BIGINT NOT NULL,
    operation_id BIGINT NOT NULL REFERENCES financial_operations(id),
    participant_code TEXT NOT NULL REFERENCES business_participants(code) CHECK (participant_code IN ('skyex','pe','daniyar')),
    client_id BIGINT NOT NULL REFERENCES clients(id),
    wallet_account_id BIGINT NOT NULL REFERENCES client_wallet_accounts(ledger_account_id),
    credit_usdt NUMERIC(36,6) NOT NULL CHECK (credit_usdt>=0),
    debit_usdt NUMERIC(36,6) NOT NULL CHECK (debit_usdt>=0),
    PRIMARY KEY(expense_id,participant_code),
    FOREIGN KEY(expense_id,operation_id) REFERENCES expenses(id,operation_id),
    CHECK (credit_usdt+debit_usdt>0)
);
CREATE TRIGGER immutable_expense_wallet_entry BEFORE INSERT OR UPDATE OR DELETE ON expense_wallet_entries
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE FUNCTION check_expense_wallet_entries() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
DECLARE e expenses; d NUMERIC; expected_credit NUMERIC; expected_debit NUMERIC; participant RECORD;
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    SELECT * INTO e FROM expenses WHERE operation_id=NEW.id AND settlement_version=2;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF e.amount_usdt<>round(e.amount_usdt,6) OR EXISTS(SELECT 1 FROM expense_obligations WHERE expense_id=e.id) THEN
        RAISE EXCEPTION 'Wallet expenses cannot have duplicate obligations or fractional wallet units' USING ERRCODE='23514';
    END IF;
    SELECT coalesce(sum(a.amount_usdt),0) INTO d FROM expense_allocations a
        JOIN business_participants p ON p.party_id=a.party_id AND p.code='daniyar' WHERE a.expense_id=e.id;
    IF coalesce((SELECT sum(l.native_amount) FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id
        WHERE l.operation_id=NEW.id AND a.purpose='operating_expense' AND l.side='debit'),0)<>e.amount_usdt-d THEN
        RAISE EXCEPTION 'Expense business share does not match journal' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM expense_wallet_entries x JOIN business_participants p ON p.code=x.participant_code
        WHERE x.expense_id=e.id AND x.credit_usdt<>CASE WHEN e.payer_party_id=p.party_id THEN e.amount_usdt ELSE 0 END)
        OR EXISTS(SELECT 1 FROM expense_wallet_entries x WHERE x.expense_id=e.id AND
            x.debit_usdt<>CASE WHEN x.participant_code='daniyar' THEN d ELSE 0 END) THEN
        RAISE EXCEPTION 'Unexpected expense wallet entry' USING ERRCODE='23514';
    END IF;
    FOR participant IN SELECT * FROM business_participants WHERE code IN ('skyex','pe','daniyar') LOOP
        expected_credit:=CASE WHEN e.payer_party_id=participant.party_id THEN e.amount_usdt ELSE 0 END;
        expected_debit:=CASE WHEN participant.code='daniyar' THEN d ELSE 0 END;
        IF expected_credit+expected_debit>0 AND NOT EXISTS(
            SELECT 1 FROM expense_wallet_entries x JOIN business_client_bindings b ON b.purpose=x.participant_code||'_balance' AND b.client_id=x.client_id
            JOIN client_wallet_accounts w ON w.client_id=x.client_id AND w.ledger_account_id=x.wallet_account_id AND w.kind='client' AND w.instrument_code='USDT'
            WHERE x.expense_id=e.id AND x.participant_code=participant.code
                AND x.credit_usdt=expected_credit AND x.debit_usdt=expected_debit) THEN
            RAISE EXCEPTION 'Expense wallet credit/debit does not match payer and shares' USING ERRCODE='23514';
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM expense_wallet_entries x WHERE x.expense_id=e.id AND (
        x.credit_usdt<>(SELECT coalesce(sum(l.native_amount),0) FROM journal_lines l WHERE l.operation_id=NEW.id AND l.account_id=x.wallet_account_id AND l.side='credit') OR
        x.debit_usdt<>(SELECT coalesce(sum(l.native_amount),0) FROM journal_lines l WHERE l.operation_id=NEW.id AND l.account_id=x.wallet_account_id AND l.side='debit'))) THEN
        RAISE EXCEPTION 'Expense wallet document does not match journal' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER expense_wallet_entry_check AFTER INSERT OR UPDATE ON financial_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_expense_wallet_entries();

-- A wallet statement includes expense effects and ordinary repayments/BestChange/exchanges.
CREATE VIEW expense_wallet_events AS
    SELECT p.party_id AS participant_party_id,p.code AS participant_code,c.id AS client_id,
        o.id AS operation_id,o.business_date,o.kind,o.reversal_of_id,
        coalesce(origin.kind,'') AS original_kind,
        coalesce(e.settlement_version,original_expense.settlement_version,0)=2 AS wallet_expense,
        sum(CASE l.side WHEN 'credit' THEN l.native_amount ELSE 0 END) AS credit_usdt,
        sum(CASE l.side WHEN 'debit' THEN l.native_amount ELSE 0 END) AS debit_usdt,
        sum(CASE l.side WHEN 'credit' THEN l.native_amount ELSE -l.native_amount END) AS delta
    FROM business_participants p
    JOIN business_client_bindings binding ON binding.purpose=p.code||'_balance'
    JOIN clients c ON c.id=binding.client_id
    JOIN client_wallet_accounts w ON w.client_id=c.id AND w.kind='client' AND w.instrument_code='USDT'
    JOIN journal_lines l ON l.account_id=w.ledger_account_id AND NOT l.valuation_component
    JOIN financial_operations o ON o.id=l.operation_id AND o.status='posted'
    LEFT JOIN expenses e ON e.operation_id=o.id
    LEFT JOIN financial_operations origin ON origin.id=o.reversal_of_id
    LEFT JOIN expenses original_expense ON original_expense.operation_id=origin.id
    GROUP BY p.party_id,p.code,c.id,o.id,origin.kind,e.settlement_version,original_expense.settlement_version;
ALTER TABLE expense_statement_rows ADD COLUMN wallet_mode BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE expense_statement_rows ADD COLUMN withheld quantity NOT NULL DEFAULT 0 CHECK (withheld>=0);
ALTER TABLE expense_statement_rows ADD COLUMN other_movements quantity NOT NULL DEFAULT 0;
DO $$ DECLARE c RECORD; BEGIN
    FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='expense_statement_rows'::regclass AND contype='c'
        AND (pg_get_constraintdef(oid) LIKE '%opening%' OR pg_get_constraintdef(oid) LIKE '%closing%') LOOP
        EXECUTE format('ALTER TABLE expense_statement_rows DROP CONSTRAINT %I',c.conname);
    END LOOP;
END $$;
ALTER TABLE expense_statement_rows ADD CHECK (closing=opening+accrued-settled-netted+corrected-withheld+other_movements);
