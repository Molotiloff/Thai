SOURCE_SQL = """
    SELECT o.id AS operation_id,o.kind,o.business_date,a.id AS account_id,a.purpose,a.account_class,
        sum(CASE l.side WHEN 'credit' THEN l.amount_usdt ELSE -l.amount_usdt END) AS credit_signed_usdt
    FROM financial_operations o JOIN journal_lines l ON l.operation_id=o.id JOIN ledger_accounts a ON a.id=l.account_id
    LEFT JOIN financial_operations origin ON origin.id=o.reversal_of_id
    WHERE o.status='posted' AND o.business_date>=$1 AND o.business_date<($1::date+interval '1 month')
        AND a.account_class IN ('income','expense') AND o.kind<>'profit_closure' AND coalesce(origin.kind,'')<>'profit_closure'
    GROUP BY o.id,o.kind,o.business_date,a.id,a.purpose,a.account_class ORDER BY o.id,a.id
"""
