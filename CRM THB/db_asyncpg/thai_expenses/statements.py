STATEMENT_SQL = """
    SELECT debtor_party_id,creditor_party_id,currency_code,
        coalesce(sum(delta) FILTER (WHERE business_date<$1),0) AS opening,
        coalesce(sum(delta) FILTER (WHERE business_date>=$1 AND kind='accrued'),0) AS accrued,
        -coalesce(sum(delta) FILTER (WHERE business_date>=$1 AND kind='settled'),0) AS settled,
        -coalesce(sum(delta) FILTER (WHERE business_date>=$1 AND kind='netted'),0) AS netted,
        coalesce(sum(delta) FILTER (WHERE business_date>=$1 AND kind='corrected'),0) AS corrected,
        sum(delta) AS closing
    FROM expense_debt_events WHERE business_date<($1::date+interval '1 month')
    GROUP BY debtor_party_id,creditor_party_id,currency_code
    ORDER BY debtor_party_id,creditor_party_id,currency_code
"""
