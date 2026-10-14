ALTER TABLE client_groups ADD CONSTRAINT nonempty_group_name CHECK (btrim(name)<>'');
ALTER TABLE client_groups ADD CONSTRAINT valid_group_code CHECK (code ~ '^[a-z][a-z0-9_]{0,39}$');
CREATE TABLE client_directory_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE CHECK (btrim(idempotency_key)<>'' AND length(idempotency_key)<=200),
    fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    kind TEXT NOT NULL CHECK (kind IN ('group_create','group_rename','client_create','client_group')),
    actor_id BIGINT NOT NULL REFERENCES users(id),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
    result JSONB NOT NULL CHECK (jsonb_typeof(result)='object'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER immutable_directory_event BEFORE UPDATE OR DELETE ON client_directory_events
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
CREATE FUNCTION protect_group_code() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.code<>OLD.code OR NEW.exposes_in_loans<>OLD.exposes_in_loans THEN
        RAISE EXCEPTION 'Client group code is immutable' USING ERRCODE='23514';
    END IF;
    IF btrim(NEW.name)='' THEN RAISE EXCEPTION 'Group name required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stable_client_group BEFORE UPDATE ON client_groups
    FOR EACH ROW EXECUTE FUNCTION protect_group_code();
CREATE FUNCTION protect_client_party() RETURNS TRIGGER LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.party_id<>OLD.party_id THEN
        RAISE EXCEPTION 'Client party identity is immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stable_client_party BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION protect_client_party();
CREATE OR REPLACE VIEW partner_loan_balances AS
    SELECT c.id AS client_id,c.party_id,w.instrument_code,b.currency_code,
        b.balance,b.carrying_usdt,w.ledger_account_id
    FROM clients c JOIN client_groups g ON g.code=c.group_code AND g.code='partner' AND g.exposes_in_loans
    JOIN client_wallet_accounts w ON w.client_id=c.id AND w.kind='client'
    JOIN posted_account_balances b ON b.account_id=w.ledger_account_id WHERE c.is_active;
INSERT INTO role_permissions(role,permission)
    SELECT role,'client.manage' FROM unnest(ARRAY['manager','accountant','owner','admin']) role;
