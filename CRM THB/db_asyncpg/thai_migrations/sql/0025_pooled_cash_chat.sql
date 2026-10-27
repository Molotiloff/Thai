-- Cash chat commands operate on the existing pooled cash, never a client wallet.
INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code,instrument_code)
    SELECT 'cash-adjustment-offset:'||code,'equity','cash_adjustment_offset','credit',currency_code,code FROM instruments;
INSERT INTO account_balances(account_id)
    SELECT id FROM ledger_accounts WHERE purpose='cash_adjustment_offset';
CREATE OR REPLACE FUNCTION check_deferred_thb() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id
        WHERE l.operation_id=NEW.id AND l.unvalued_native>0 AND a.purpose NOT IN ('cash','client_wallet','wallet_adjustment_offset','cash_adjustment_offset')) THEN
        RAISE EXCEPTION 'Deferred funds need cash or client wallet account' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM journal_lines l WHERE l.operation_id=NEW.id AND l.valuation_component
        GROUP BY l.account_id HAVING sum(CASE l.side WHEN 'debit' THEN l.native_amount ELSE -l.native_amount END)<>0) THEN
        RAISE EXCEPTION 'Valuation must preserve native balances' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM posted_account_balances b JOIN ledger_accounts a ON a.id=b.account_id
        WHERE ((a.purpose='cash' AND b.unvalued_native<0) OR b.balance*b.unvalued_native<0 OR abs(b.unvalued_native)>abs(b.balance))
            AND a.purpose IN ('cash','client_wallet','wallet_adjustment_offset','cash_adjustment_offset')) THEN
        RAISE EXCEPTION 'Invalid unvalued native balance' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM cash_accounts c JOIN posted_account_balances b ON b.account_id=c.ledger_account_id
        JOIN inventory_balances i ON i.instrument_code=c.instrument_code
        WHERE b.unvalued_native<>i.unvalued_quantity) THEN
        RAISE EXCEPTION 'Cash and inventory unvalued funds differ' USING ERRCODE='23514';
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

ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status','cash_request_confirm',
        'client_transfer_request','client_transfer_resolve','client_transfer_adjust',
        'wallet_undo','cash_undo','wallet_zero_request','wallet_zero_resolve',
        'best_change_correct','payment_watch_start','payment_watch_control','payment_wallet_set',
        'act_adjust','manager_change'));

-- Owners and administrators keep access to the restored cashier actions.
INSERT INTO role_permissions(role,permission) VALUES ('owner','cash.execute'),('admin','cash.execute')
    ON CONFLICT DO NOTHING;
