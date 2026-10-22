-- Records the wallet actually credited to Daniyar (legacy buffer or personal wallet).
-- Existing journal entries and pending historical allocations are unchanged.
ALTER TABLE best_change_calculations RENAME COLUMN buffer_wallet_account_id TO daniyar_wallet_account_id;
