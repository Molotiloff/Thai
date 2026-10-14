ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status','cash_request_confirm',
        'client_transfer_request','client_transfer_resolve','client_transfer_adjust',
        'wallet_undo','wallet_zero_request','wallet_zero_resolve',
        'best_change_correct','payment_watch_start','payment_watch_control','payment_wallet_set',
        'act_adjust','manager_change'));
