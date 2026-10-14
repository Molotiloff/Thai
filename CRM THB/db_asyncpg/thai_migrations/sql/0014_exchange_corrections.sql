ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status'));
CREATE INDEX exchange_request_message_links ON telegram_message_links(deal_id,chat_id)
    WHERE purpose IN ('exchange_client','exchange_request');
CREATE UNIQUE INDEX exchange_replacement_parent ON deal_reversals(replacement_deal_id)
    WHERE replacement_deal_id IS NOT NULL;
