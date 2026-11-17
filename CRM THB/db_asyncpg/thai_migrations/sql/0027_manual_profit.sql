-- Explicit historical P&L, independent of cash and client wallet movements.
INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code)
VALUES ('manual-profit-income','income','manual_profit_income','credit','USDT'),
       ('manual-profit-loss','expense','manual_profit_loss','debit','USDT'),
       ('historical-profit-offset','equity','historical_profit_offset','debit','USDT');
INSERT INTO account_balances(account_id)
SELECT id FROM ledger_accounts WHERE purpose IN ('manual_profit_income','manual_profit_loss','historical_profit_offset');

CREATE OR REPLACE FUNCTION check_trade_posting() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM deals d WHERE d.operation_id=NEW.id AND (d.status<>'posted'
        OR (SELECT count(*) FROM deal_legs WHERE deal_id=d.id)<>
            CASE WHEN d.kind='manual_profit' THEN 0 WHEN d.kind IN ('deposit','withdrawal') THEN 1 ELSE 2 END)) THEN
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
    IF EXISTS(SELECT 1 FROM deals d WHERE d.operation_id=NEW.id AND d.kind='cash_exchange'
        AND (NEW.kind<>'fx_trade' OR d.attributes->>'mode'<>'cash'
            OR (NEW.command_details->>'cash_request_id')::bigint IS DISTINCT FROM d.id
            OR (SELECT count(*) FROM cash_exchange_confirmations WHERE deal_id=d.id)<>2
            OR EXISTS(SELECT 1 FROM cash_exchange_confirmations c WHERE c.deal_id=d.id
                AND (c.instrument_code IS DISTINCT FROM d.attributes->c.direction->>'instrument'
                    OR c.quantity IS DISTINCT FROM (d.attributes->c.direction->>'quantity')::numeric))
            OR EXISTS(SELECT 1 FROM deal_legs l WHERE l.deal_id=d.id
                AND (l.instrument_code IS DISTINCT FROM d.attributes->l.direction->>'instrument'
                    OR l.quantity IS DISTINCT FROM (d.attributes->l.direction->>'quantity')::numeric)))) THEN
        RAISE EXCEPTION 'Cash exchange needs both matching confirmations' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION check_manual_profit_posting() RETURNS TRIGGER LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE d deals%ROWTYPE; amount NUMERIC;
BEGIN
    IF NEW.status<>'posted' THEN RETURN NULL; END IF;
    SELECT * INTO d FROM deals WHERE operation_id=NEW.id AND kind='manual_profit';
    IF NEW.kind='manual_profit' OR d.id IS NOT NULL THEN
        IF d.id IS NULL OR NEW.kind<>'manual_profit' OR NEW.posting_mode<>'ledger'
            OR NEW.command_details->>'deal_id' IS DISTINCT FROM d.id::text
            OR (NEW.command_details->>'client_id')::bigint IS DISTINCT FROM d.client_id
            OR NEW.command_details->'attributes' IS DISTINCT FROM d.attributes
            OR d.attributes->>'business_date' IS DISTINCT FROM NEW.business_date::text THEN
            RAISE EXCEPTION 'Manual profit needs its matching immutable deal' USING ERRCODE='23514';
        END IF;
        amount := (d.attributes->>'amount_usdt')::numeric;
        IF amount IS NULL OR amount=0 OR d.attributes->>'comment' IS NULL
            OR ((d.attributes->>'thb_amount')::numeric IS NOT NULL AND
                ((d.attributes->>'thb_amount')::numeric<=0 OR
                 round((d.attributes->>'thb_amount')::numeric,2)<>(d.attributes->>'thb_amount')::numeric))
            OR (SELECT count(*) FROM journal_lines WHERE operation_id=NEW.id)<>2
            OR NOT EXISTS(SELECT 1 FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id
                WHERE l.operation_id=NEW.id AND a.purpose='historical_profit_offset'
                    AND l.side=CASE WHEN amount>0 THEN 'debit' ELSE 'credit' END
                    AND l.currency_code='USDT' AND l.native_amount=abs(amount) AND l.amount_usdt=abs(amount))
            OR NOT EXISTS(SELECT 1 FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id
                WHERE l.operation_id=NEW.id
                    AND a.purpose=CASE WHEN amount>0 THEN 'manual_profit_income' ELSE 'manual_profit_loss' END
                    AND l.side=CASE WHEN amount>0 THEN 'credit' ELSE 'debit' END
                    AND l.currency_code='USDT' AND l.native_amount=abs(amount) AND l.amount_usdt=abs(amount)) THEN
            RAISE EXCEPTION 'Manual profit must only post the declared P&L and historical offset' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER manual_profit_posting AFTER INSERT OR UPDATE ON financial_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_manual_profit_posting();
