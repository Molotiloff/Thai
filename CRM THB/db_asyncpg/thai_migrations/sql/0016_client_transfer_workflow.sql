ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status','cash_request_confirm',
        'client_transfer_request','client_transfer_resolve','client_transfer_adjust'));
CREATE INDEX client_transfer_versions ON financial_operations
    (((command_details->>'transfer_root_id')::bigint)) WHERE kind='wallet_transfer';
CREATE UNIQUE INDEX client_transfer_message_delivery ON telegram_message_links(operation_id,chat_id,purpose)
    WHERE purpose IN ('client_transfer_receipt_posted','client_transfer_receipt_cancelled',
        'client_transfer_text_posted','client_transfer_text_cancelled');
