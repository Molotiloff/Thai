-- Preserve the classification and bindings used by posted money history.
CREATE FUNCTION protect_used_account() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM journal_lines l JOIN financial_operations o ON o.id=l.operation_id
        WHERE l.account_id=OLD.id AND o.status='posted') AND (
        TG_OP='DELETE' OR NEW.id<>OLD.id OR NEW.account_class<>OLD.account_class
        OR NEW.normal_side<>OLD.normal_side OR NEW.purpose<>OLD.purpose
        OR NEW.currency_code<>OLD.currency_code OR NEW.party_id IS DISTINCT FROM OLD.party_id
        OR NEW.instrument_code IS DISTINCT FROM OLD.instrument_code) THEN
        RAISE EXCEPTION 'Used account classification is immutable' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER used_account BEFORE UPDATE OR DELETE ON ledger_accounts
    FOR EACH ROW EXECUTE FUNCTION protect_used_account();
CREATE TRIGGER immutable_cash_binding BEFORE UPDATE OR DELETE ON cash_accounts
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
CREATE TRIGGER immutable_wallet_binding BEFORE UPDATE OR DELETE ON client_wallet_accounts
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot();

