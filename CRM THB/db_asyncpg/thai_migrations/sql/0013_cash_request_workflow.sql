-- Cash requests use existing deals, message links and outbox; only extend the audit vocabulary.
ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch',
        'cash_request_create','cash_request_edit','cash_request_status'));
CREATE INDEX cash_request_message_links ON telegram_message_links(deal_id,chat_id)
    WHERE purpose IN ('cash_client','cash_request');

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
    RETURN NULL;
END $$;
