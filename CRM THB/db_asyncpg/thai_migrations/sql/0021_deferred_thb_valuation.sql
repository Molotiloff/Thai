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
