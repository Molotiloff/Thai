CREATE TABLE cash_exchange_confirmations (
    deal_id BIGINT NOT NULL REFERENCES deals(id),
    direction TEXT NOT NULL CHECK (direction IN ('receive','pay')),
    instrument_code TEXT NOT NULL REFERENCES instruments(code),
    quantity quantity NOT NULL CHECK (quantity>0),
    quoted_rate NUMERIC CHECK (quoted_rate>0),
    actor_id BIGINT NOT NULL REFERENCES users(id),
    confirmed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(deal_id,direction)
);
CREATE FUNCTION validate_cash_exchange_confirmation() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE request deals%ROWTYPE;
BEGIN
    SELECT * INTO request FROM deals WHERE id=NEW.deal_id FOR UPDATE;
    IF request.id IS NULL OR request.kind<>'cash_exchange' OR request.status<>'ready'
        OR NEW.instrument_code IS DISTINCT FROM request.attributes->NEW.direction->>'instrument'
        OR NEW.quantity IS DISTINCT FROM (request.attributes->NEW.direction->>'quantity')::numeric THEN
        RAISE EXCEPTION 'Confirmation differs from ready cash exchange' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER valid_cash_exchange_confirmation BEFORE INSERT ON cash_exchange_confirmations
    FOR EACH ROW EXECUTE FUNCTION validate_cash_exchange_confirmation();
CREATE TRIGGER immutable_cash_exchange_confirmation BEFORE UPDATE OR DELETE ON cash_exchange_confirmations
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status','cash_request_confirm'));

CREATE OR REPLACE FUNCTION check_trade_posting() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM deals d WHERE d.operation_id=NEW.id AND (d.status<>'posted'
        OR (SELECT count(*) FROM deal_legs WHERE deal_id=d.id)<>
            CASE WHEN d.kind IN ('deposit','withdrawal') THEN 1 ELSE 2 END)) THEN
        RAISE EXCEPTION 'Posted request needs its contract legs' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM deals d WHERE d.operation_id=NEW.id AND d.kind IN ('deposit','withdrawal')
        AND (NEW.kind<>CASE WHEN d.kind='deposit' THEN 'wallet_deposit' ELSE 'wallet_withdrawal' END
          OR (NEW.command_details->>'request_id')::bigint IS DISTINCT FROM d.id
          OR (NEW.command_details->>'client')::bigint IS DISTINCT FROM d.client_id
          OR NEW.command_details->>'instrument' IS DISTINCT FROM d.attributes->>'instrument'
          OR (NEW.command_details->>'quantity')::numeric IS DISTINCT FROM (d.attributes->>'quantity')::numeric
          OR NOT EXISTS(SELECT 1 FROM deal_legs l WHERE l.deal_id=d.id
              AND l.instrument_code=d.attributes->>'instrument'
              AND l.quantity=(d.attributes->>'quantity')::numeric
              AND l.direction=CASE WHEN d.kind='deposit' THEN 'receive' ELSE 'pay' END))) THEN
        RAISE EXCEPTION 'Cash settlement differs from request contract' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM deals d WHERE d.operation_id=NEW.id AND d.kind='cash_exchange'
        AND (NEW.kind<>'fx_trade' OR d.attributes->>'mode'<>'cash'
            OR (NEW.command_details->>'cash_request_id')::bigint IS DISTINCT FROM d.id
            OR (SELECT count(*) FROM cash_exchange_confirmations WHERE deal_id=d.id)<>2
            OR EXISTS(SELECT 1 FROM cash_exchange_confirmations c WHERE c.deal_id=d.id
                AND (c.instrument_code IS DISTINCT FROM d.attributes->c.direction->>'instrument'
                    OR c.quantity IS DISTINCT FROM (d.attributes->c.direction->>'quantity')::numeric))
            OR EXISTS(SELECT 1 FROM deal_legs l WHERE l.deal_id=d.id
                AND (l.instrument_code IS DISTINCT FROM d.attributes->l.direction->>'instrument'
                    OR l.quantity IS DISTINCT FROM (d.attributes->l.direction->>'quantity')::numeric)))) THEN
        RAISE EXCEPTION 'Cash exchange needs both matching confirmations' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
