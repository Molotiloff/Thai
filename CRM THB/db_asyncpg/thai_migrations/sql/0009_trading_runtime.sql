ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch'));
CREATE TABLE market_quotes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_code TEXT NOT NULL REFERENCES instruments(code),
    rate_snapshot_id BIGINT NOT NULL REFERENCES fx_rate_snapshots(id),
    operation_id BIGINT UNIQUE NOT NULL REFERENCES financial_operations(id)
);
CREATE TRIGGER immutable_market_quote BEFORE INSERT OR UPDATE OR DELETE ON market_quotes
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE INDEX quotes_by_instrument ON market_quotes(instrument_code,id DESC);
CREATE TABLE deal_reversals (
    deal_id BIGINT UNIQUE NOT NULL REFERENCES deals(id),
    operation_id BIGINT UNIQUE NOT NULL REFERENCES financial_operations(id),
    reason TEXT NOT NULL CHECK (btrim(reason)<>''),
    replacement_deal_id BIGINT REFERENCES deals(id),
    PRIMARY KEY(deal_id,operation_id)
);
CREATE TRIGGER immutable_deal_reversal BEFORE INSERT OR UPDATE OR DELETE ON deal_reversals
    FOR EACH ROW EXECUTE FUNCTION protect_financial_document();
CREATE VIEW effective_deals AS
    SELECT d.*,CASE WHEN r.operation_id IS NOT NULL THEN 'cancelled' ELSE d.status END AS effective_status,
        r.operation_id AS reversal_operation_id,r.replacement_deal_id
    FROM deals d LEFT JOIN deal_reversals r ON r.deal_id=d.id
    JOIN financial_operations ro ON ro.id=r.operation_id AND ro.status='posted'
    UNION ALL
    SELECT d.*,d.status,NULL::bigint,NULL::bigint FROM deals d
    WHERE NOT EXISTS(SELECT 1 FROM deal_reversals r JOIN financial_operations o ON o.id=r.operation_id
        WHERE r.deal_id=d.id AND o.status='posted');
INSERT INTO role_permissions(role,permission)
    SELECT role,'rates.manage' FROM unnest(ARRAY['manager','accountant','owner','admin']) role;
CREATE FUNCTION check_trade_posting() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM deals d WHERE d.operation_id=NEW.id AND (d.status<>'posted'
        OR (SELECT count(*) FROM deal_legs WHERE deal_id=d.id)<>2)) THEN
        RAISE EXCEPTION 'Posted trade needs both contract legs' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER trade_posting_check AFTER INSERT OR UPDATE ON financial_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_trade_posting();
CREATE TRIGGER immutable_confirmed_transfer BEFORE UPDATE OR DELETE ON payment_watch_events
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
CREATE TABLE telegram_deliveries (
    event_key TEXT NOT NULL REFERENCES telegram_outbox(event_key),
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(event_key,chat_id)
);
