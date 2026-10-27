STATEMENT_SQL = """
    WITH events AS (
        SELECT debtor_party_id,creditor_party_id,currency_code,business_date,
            false AS wallet_mode,delta,
            CASE WHEN kind='accrued' THEN delta ELSE 0 END AS accrued,
            CASE WHEN kind='settled' THEN -delta ELSE 0 END AS settled,
            CASE WHEN kind='netted' THEN -delta ELSE 0 END AS netted,
            CASE WHEN kind='corrected' THEN delta ELSE 0 END AS corrected,
            0 AS withheld,0 AS other_movements
        FROM expense_debt_events
        UNION ALL
        SELECT business.party_id,e.participant_party_id,'USDT',e.business_date,true,e.delta,
            CASE WHEN e.wallet_expense AND e.reversal_of_id IS NULL THEN e.credit_usdt ELSE 0 END,
            CASE WHEN NOT e.wallet_expense AND e.delta<0 THEN -e.delta ELSE 0 END,
            0,
            CASE WHEN e.wallet_expense AND e.reversal_of_id IS NOT NULL THEN e.delta ELSE 0 END,
            CASE WHEN e.wallet_expense AND e.reversal_of_id IS NULL THEN e.debit_usdt ELSE 0 END,
            CASE WHEN NOT e.wallet_expense AND e.delta>0 THEN e.delta ELSE 0 END
        FROM expense_wallet_events e CROSS JOIN business_participants business WHERE business.code='business'
    )
    SELECT debtor_party_id,creditor_party_id,currency_code,wallet_mode,
        coalesce(sum(delta) FILTER (WHERE business_date<$1),0) AS opening,
        coalesce(sum(accrued) FILTER (WHERE business_date>=$1),0) AS accrued,
        coalesce(sum(settled) FILTER (WHERE business_date>=$1),0) AS settled,
        coalesce(sum(netted) FILTER (WHERE business_date>=$1),0) AS netted,
        coalesce(sum(corrected) FILTER (WHERE business_date>=$1),0) AS corrected,
        coalesce(sum(withheld) FILTER (WHERE business_date>=$1),0) AS withheld,
        coalesce(sum(other_movements) FILTER (WHERE business_date>=$1),0) AS other_movements,
        sum(delta) AS closing
    FROM events WHERE business_date<($1::date+interval '1 month')
    GROUP BY debtor_party_id,creditor_party_id,currency_code,wallet_mode
    ORDER BY debtor_party_id,creditor_party_id,currency_code
"""
