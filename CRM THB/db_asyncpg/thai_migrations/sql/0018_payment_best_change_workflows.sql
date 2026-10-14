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
