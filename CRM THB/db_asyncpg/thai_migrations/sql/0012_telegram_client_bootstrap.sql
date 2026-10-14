-- Telegram customers are not CRM staff. Audit registration without impersonating a user.
CREATE TABLE telegram_client_bootstrap_events (
    idempotency_key TEXT PRIMARY KEY CHECK (btrim(idempotency_key)<>'' AND length(idempotency_key)<=200),
    fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    telegram_user_id BIGINT NOT NULL CHECK (telegram_user_id>0),
    telegram_chat_id BIGINT NOT NULL CHECK (telegram_chat_id<>0),
    client_id BIGINT NOT NULL REFERENCES clients(id),
    name_snapshot TEXT NOT NULL CHECK (btrim(name_snapshot)<>''),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER immutable_telegram_bootstrap BEFORE UPDATE OR DELETE ON telegram_client_bootstrap_events
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
