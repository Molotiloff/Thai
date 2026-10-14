-- Run after Thai bootstrap and BEFORE starting main.py.
-- psql -X -h 127.0.0.1 -p 5433 -U thai_crm_app -d thai_crm -W -v ON_ERROR_STOP=1 -f deploy/thai/verify_initial_database.sql
\set ON_ERROR_STOP on
BEGIN READ ONLY;
DO $$
DECLARE
    table_name TEXT;
    row_count BIGINT;
BEGIN
    IF inet_server_port() IS DISTINCT FROM 5433 THEN
        RAISE EXCEPTION 'Expected the isolated PostgreSQL server on port 5433';
    END IF;
    IF (SELECT count(*) FROM alembic_version_thai) <> 1
        OR (SELECT version_num FROM alembic_version_thai LIMIT 1) <> 'thai_0022' THEN
        RAISE EXCEPTION 'Expected Thai revision thai_0022';
    END IF;
    IF (SELECT count(*) FROM crm_schema_identity) <> 1 OR NOT EXISTS (
        SELECT 1 FROM crm_schema_identity WHERE family='thai'
            AND accounting_currency='USDT' AND operational_currency='THB'
            AND business_timezone='Asia/Bangkok'
    ) THEN
        RAISE EXCEPTION 'Unexpected accounting identity';
    END IF;
    -- Currency codes use a fixed byte order, independent of the cluster locale.
    IF (SELECT array_agg(code::TEXT ORDER BY code COLLATE "C") FROM instruments)
        IS DISTINCT FROM ARRAY['EUR','EUR500','RUB','THB_BANK','THB_CASH','USDT','USD_BL','USD_WH']::TEXT[]
        OR (SELECT count(*) FROM cash_accounts) <> 8
        OR (SELECT count(*) FROM instruments WHERE storage_kind='bank') <> 1 THEN
        RAISE EXCEPTION 'Expected 8 instruments/cash accounts and one bank instrument';
    END IF;
    FOREACH table_name IN ARRAY ARRAY[
        'clients', 'client_chat_links', 'financial_operations', 'journal_lines',
        'fx_rate_snapshots', 'capital_contributions', 'expenses', 'deals',
        'investor_interest_accruals', 'profit_period_closures', 'user_roles'
    ] LOOP
        EXECUTE format('SELECT count(*) FROM %I', table_name) INTO row_count;
        IF row_count <> 0 THEN
            RAISE EXCEPTION 'Expected empty table %, got % rows', table_name, row_count;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM posted_account_balances WHERE balance<>0)
        OR EXISTS (SELECT 1 FROM inventory_balances WHERE quantity<>0 OR cost_usdt<>0)
        OR EXISTS (SELECT 1 FROM users WHERE is_active OR telegram_user_id IS NOT NULL)
        OR EXISTS (SELECT 1 FROM capital_interest_terms WHERE monthly_interest_percent<>0) THEN
        RAISE EXCEPTION 'Expected zero balances and no active users/investor accrual terms';
    END IF;
END $$;
SELECT current_database() AS database, current_user AS db_user, inet_server_port() AS port;
SELECT version_num AS revision FROM alembic_version_thai;
SELECT family, accounting_currency, operational_currency, business_timezone FROM crm_schema_identity;
SELECT code FROM instruments ORDER BY code COLLATE "C";
SELECT 'Initial Thai database verified; no financial or client data imported.' AS result;
COMMIT;
