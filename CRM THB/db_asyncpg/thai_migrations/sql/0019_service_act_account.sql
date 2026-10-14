ALTER TABLE client_directory_events DROP CONSTRAINT client_directory_events_kind_check;
ALTER TABLE client_directory_events ADD CONSTRAINT client_directory_events_kind_check
    CHECK (kind IN ('group_create','group_rename','client_create','client_group','chat_bind','client_close',
        'trade_create','trade_ready','trade_cancel','trade_watch','trade_correct',
        'cash_request_create','cash_request_edit','cash_request_status','cash_request_confirm',
        'client_transfer_request','client_transfer_resolve','client_transfer_adjust',
        'wallet_undo','wallet_zero_request','wallet_zero_resolve',
        'best_change_correct','payment_watch_start','payment_watch_control','payment_wallet_set','act_adjust'));

CREATE TABLE act_counter_adjustments (
    event_key TEXT PRIMARY KEY REFERENCES client_directory_events(idempotency_key)
        DEFERRABLE INITIALLY DEFERRED,
    request_chat_id BIGINT NOT NULL,
    actor_id BIGINT NOT NULL REFERENCES users(id),
    delta quantity NOT NULL CHECK (delta=round(delta,6)),
    target_amount quantity NOT NULL CHECK (target_amount=round(target_amount,6)),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX act_counter_chat ON act_counter_adjustments(request_chat_id);
CREATE TRIGGER immutable_act_adjustment BEFORE UPDATE OR DELETE ON act_counter_adjustments
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
INSERT INTO role_permissions(role,permission)
    SELECT role,'act.reconcile' FROM unnest(ARRAY['manager','accountant','owner','admin']) role;
