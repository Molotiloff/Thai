-- Records the wallet actually credited to Daniyar (legacy buffer or personal wallet).
-- Existing journal entries and pending historical allocations are unchanged.
ALTER TABLE best_change_calculations RENAME COLUMN buffer_wallet_account_id TO daniyar_wallet_account_id;
CREATE UNIQUE INDEX best_change_profit_message_delivery ON telegram_message_links(deal_id,chat_id) WHERE purpose='best_change_profit';
