-- New business reference data only. No real operators, clients, chats, FX rates or funds.
INSERT INTO currencies(code,name,precision) VALUES
    ('THB','Тайский бат',2),('USDT','USDT',6),('USD','Доллар США',2),
    ('EUR','Евро',2),('RUB','Российский рубль',2);
INSERT INTO instruments(code,currency_code,name,storage_kind,precision) VALUES
    ('THB_CASH','THB','Наличный бат','cash',2),('THB_BANK','THB','Безналичный бат','bank',2),
    ('USDT','USDT','USDT','crypto',6),('USD_WH','USD','Доллар белый','cash',2),
    ('USD_BL','USD','Доллар синий','cash',2),('EUR','EUR','Евро','cash',2),
    ('EUR500','EUR','Евро 500','cash',2),('RUB','RUB','Рубль','cash',2);
INSERT INTO instrument_aliases(alias,instrument_code) VALUES
    ('thb','THB_CASH'),('thb_cash','THB_CASH'),('бат','THB_CASH'),('батнал','THB_CASH'),
    ('thbb','THB_BANK'),('thb_bank','THB_BANK'),('батбезнал','THB_BANK'),
    ('usdt','USDT'),('юсдт','USDT'),('usdw','USD_WH'),('usd_wh','USD_WH'),('долбел','USD_WH'),
    ('usd','USD_BL'),('usdb','USD_BL'),('usdbl','USD_BL'),('usd_bl','USD_BL'),('долсин','USD_BL'),
    ('eur','EUR'),('евро','EUR'),('eur500','EUR500'),('eur 500','EUR500'),('евро500','EUR500'),
    ('евро 500','EUR500'),('rub','RUB'),('руб','RUB');
INSERT INTO client_groups(code,name,exposes_in_loans) VALUES
    ('balance','Баланс',FALSE),('partner','Партнер',TRUE);
INSERT INTO access_roles(code,name) VALUES
    ('cashier','Кассир'),('manager','Менеджер'),('accountant','Бухгалтер'),
    ('owner','Владелец'),('admin','Администратор');
INSERT INTO role_permissions(role,permission)
    SELECT role,permission FROM unnest(ARRAY['manager','accountant','owner','admin']) AS role
    CROSS JOIN unnest(ARRAY['deal.post','capital.post','expense.post','interest.approve','period.close']) AS permission;
INSERT INTO role_permissions(role,permission)
    SELECT role,permission FROM unnest(ARRAY['owner','admin']) AS role
    CROSS JOIN unnest(ARRAY['access.manage','terms.manage','period.revise']) AS permission;
INSERT INTO role_permissions(role,permission) VALUES ('cashier','cash.execute');
INSERT INTO crm_settings(key,value) VALUES
    ('wallet_negative_policy','"allowed"'),('loss_policy','"report_only"'),
    ('capital_return_policy','"same_currency_same_form_except_thb"'),
    ('investor_shortfall_policy','"manager_decision"');
DO $$
DECLARE business BIGINT; sky BIGINT; pe BIGINT; daniyar BIGINT; bootstrap_actor_id BIGINT;
        business_day DATE := (now() AT TIME ZONE 'Asia/Bangkok')::date;
        investor BIGINT; agreement BIGINT;
BEGIN
    INSERT INTO parties(name,kind) VALUES ('Обменник Таиланд','business') RETURNING id INTO business;
    INSERT INTO parties(name,kind) VALUES ('SkyEX','organization') RETURNING id INTO sky;
    INSERT INTO parties(name,kind) VALUES ('ПЕ','person') RETURNING id INTO pe;
    INSERT INTO parties(name,kind) VALUES ('Данияр','person') RETURNING id INTO daniyar;
    INSERT INTO business_participants(code,party_id) VALUES
        ('business',business),('skyex',sky),('pe',pe),('daniyar',daniyar);
    INSERT INTO party_roles(party_id,role) VALUES
        (sky,'owner'),(sky,'investor'),(pe,'owner'),(pe,'investor');
    -- FK author of initial terms only: inactive, no Telegram ID and no access roles.
    INSERT INTO users(display_name,is_active) VALUES ('System bootstrap',FALSE) RETURNING id INTO bootstrap_actor_id;
    INSERT INTO profit_share_terms(valid_from,skyex_percent,pe_percent,created_by)
        VALUES (business_day,50,50,bootstrap_actor_id);
    FOREACH investor IN ARRAY ARRAY[sky,pe] LOOP
        INSERT INTO capital_agreements(investor_party_id,principal_kind,opened_on)
            VALUES (investor,'owner_equity',business_day) RETURNING id INTO agreement;
        INSERT INTO capital_interest_terms(agreement_id,monthly_interest_percent,valid_from,created_by)
            VALUES (agreement,0,business_day,bootstrap_actor_id);
    END LOOP;
END $$;
INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code,instrument_code)
    SELECT 'cash:'||code,'asset','cash','debit',currency_code,code FROM instruments;
INSERT INTO cash_accounts(instrument_code,ledger_account_id)
    SELECT instrument_code,id FROM ledger_accounts WHERE purpose='cash';
INSERT INTO inventory_balances(instrument_code) SELECT code FROM instruments;
INSERT INTO account_balances(account_id) SELECT id FROM ledger_accounts;
INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,currency_code)
    SELECT purpose,account_class,purpose,normal_side,'USDT'
    FROM (VALUES
        ('trading_income','income','credit'),('trading_loss','expense','debit'),
        ('transfer_fee_income','income','credit'),('operating_expense','expense','debit'),
        ('platform_commission_expense','expense','debit'),('investor_interest_expense','expense','debit'),
        ('fx_income','income','credit'),('fx_loss','expense','debit'),
        ('period_result','equity','credit'),('retained_result','equity','credit')
    ) AS accounts(purpose,account_class,normal_side);
INSERT INTO account_balances(account_id)
    SELECT id FROM ledger_accounts WHERE instrument_code IS NULL;
