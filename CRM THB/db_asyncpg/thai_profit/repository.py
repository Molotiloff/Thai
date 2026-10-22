from db_asyncpg.thai_finance.repositories import JournalRepository
from db_asyncpg.thai_profit.report import SOURCE_SQL
from domain.finance.models import InvalidPosting, ReversalBlocked, canonical_json


class ProfitRepository:
    def __init__(self, connection):
        self._db = connection

    async def permission(self, actor_id, permission):
        await JournalRepository(self._db)._permission(actor_id, permission)

    async def bindings(self):
        rows = await self._db.fetch("""
            SELECT b.purpose,b.client_id,w.ledger_account_id FROM business_client_bindings b
                JOIN client_wallet_accounts w ON w.client_id=b.client_id AND w.instrument_code='USDT'
                    AND w.kind=CASE b.purpose WHEN 'best_change_buffer' THEN 'profit_buffer' ELSE 'client' END
        """)
        result = {row["purpose"]: dict(row) for row in rows}
        if len(result) != 2:
            raise InvalidPosting("Сначала настройте клиентов BestChange и Баланс Данияр")
        return result

    async def configure(self, operation_id, buffer_client_id, daniyar_client_id):
        if buffer_client_id == daniyar_client_id:
            raise InvalidPosting("Нужны два разных клиента")
        current = await self.binding_clients()
        desired = {"best_change_buffer": buffer_client_id, "daniyar_balance": daniyar_client_id}
        if current and current != desired:
            raise InvalidPosting("Привязки с финансовой историей не переназначаются")
        party = await self._db.fetchval(
            "SELECT party_id FROM clients WHERE id=$1", buffer_client_id
        )
        account = await self._db.fetchval(
            """
            INSERT INTO ledger_accounts(code,account_class,purpose,normal_side,party_id,currency_code,instrument_code)
            VALUES ($1,'liability','best_change_buffer','credit',$2,'USDT','USDT') ON CONFLICT (code) DO UPDATE SET code=excluded.code RETURNING id
        """,
            f"profit-buffer:{buffer_client_id}:USDT",
            party,
        )
        await self._db.execute(
            "INSERT INTO client_wallet_accounts(client_id,instrument_code,kind,ledger_account_id) VALUES ($1,'USDT','profit_buffer',$2) ON CONFLICT DO NOTHING",
            buffer_client_id,
            account,
        )
        await self._db.execute(
            "INSERT INTO account_balances(account_id) VALUES ($1) ON CONFLICT DO NOTHING", account
        )
        for purpose, client_id in desired.items():
            await self._db.execute(
                "INSERT INTO business_client_bindings VALUES ($1,$2) ON CONFLICT DO NOTHING",
                purpose,
                client_id,
            )
        await self._db.execute(
            "INSERT INTO operation_audit_notes VALUES ($1,$2)",
            operation_id,
            canonical_json(desired),
        )

    async def binding_clients(self):
        return {row['purpose']:row['client_id'] for row in await self._db.fetch('SELECT * FROM business_client_bindings')}

    async def rate(self, quote, actor_id, effective_at):
        return await self._db.fetchval(
            """
            INSERT INTO fx_rate_snapshots(currency_code,instrument_code,quoted_direction,quoted_rate,usdt_per_unit,effective_at,created_by)
                VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
        """,
            quote.currency,
            quote.instrument,
            quote.direction,
            quote.quoted_rate,
            quote.usdt_per_unit,
            effective_at,
            actor_id,
        )

    async def best_change(self, operation_id, command, result, bindings, attributes):
        from db_asyncpg.thai_trading.repository import TradingRepository

        deal = await self._db.fetchval(
            """
            INSERT INTO deals(client_id,kind,source,status,external_reference,created_by,attributes)
                VALUES ($1,'best_change','thai','ready',$2,$3,$4::jsonb) RETURNING id
        """,
            command["client_id"],
            command["reference"],
            command["actor_id"],
            canonical_json(attributes),
        )
        await TradingRepository(self._db).post(deal, operation_id, attributes)
        quote = (
            attributes["receive"]
            if attributes["receive"]["instrument"].startswith("THB")
            else attributes["pay"]
        )
        rate_id = await self._db.fetchval(
            "SELECT rate_snapshot_id FROM deal_legs WHERE deal_id=$1 AND instrument_code=$2",
            deal,
            quote["instrument"],
        )
        await self._db.execute(
            """
            INSERT INTO best_change_calculations VALUES ($1,'THB',$2,$3,$4,30,$5,$6,$7,$8,$9,$10,'thai-thb-usdt-v1',$11)
        """,
            deal,
            result.quantity,
            result.market_rate,
            result.client_rate,
            result.gross_thb,
            result.fee_usdt,
            result.pool_usdt,
            result.daniyar_usdt,
            result.business_usdt,
            rate_id,
            bindings["daniyar_balance"]["ledger_account_id"],
        )
        if command["correction_of"] is not None:
            await self._db.execute(
                "INSERT INTO best_change_corrections VALUES ($1,$2,$3)",
                command["correction_of"],
                deal,
                operation_id,
            )
        return deal

    async def correction(self, deal_id):
        if not await self._db.fetchval(
            """
            SELECT EXISTS(SELECT 1 FROM best_change_calculations b JOIN deals d ON d.id=b.deal_id
                WHERE b.deal_id=$1 AND NOT EXISTS(SELECT 1 FROM active_financial_originals o WHERE o.id=d.operation_id)
                    AND NOT EXISTS(SELECT 1 FROM best_change_corrections c WHERE c.original_deal_id=b.deal_id))
        """,
            deal_id,
        ):
            raise InvalidPosting("Замена требует отменённой сделки без другой замены")

    async def sources(self, period):
        return [dict(row) for row in await self._db.fetch(SOURCE_SQL, period)]

    async def term(self, period):
        row = await self._db.fetchrow(
            """
            SELECT t.* FROM profit_share_terms t LEFT JOIN active_financial_originals o ON o.id=t.operation_id
            WHERE t.valid_from<($1::date+interval '1 month') AND (t.operation_id IS NULL OR o.id IS NOT NULL)
            ORDER BY (t.operation_id IS NOT NULL) DESC,t.valid_from DESC,t.id DESC LIMIT 1
        """,
            period,
        )
        if row is None:
            raise InvalidPosting("Нет условий долей собственников для этого месяца")
        return dict(row)

    async def set_term(
        self, operation_id, actor_id, valid_from, skyex_percent, pe_percent, reason, revision_id
    ):
        if not reason.strip():
            raise InvalidPosting("Причина изменения долей обязательна")
        if (
            await self._db.fetchval(
                "SELECT EXISTS(SELECT 1 FROM period_locks WHERE context='ledger' AND period>=date_trunc('month',$1::date)::date)",
                valid_from,
            )
            and not revision_id
        ):
            raise InvalidPosting("Исторические доли изменяются только в ревизии")
        if revision_id and not await self._db.fetchval(
            "SELECT EXISTS(SELECT 1 FROM profit_period_revisions WHERE id=$1 AND period=date_trunc('month',$2::date)::date)",
            revision_id,
            valid_from,
        ):
            raise InvalidPosting("Дата действия долей должна относиться к месяцу ревизии")
        previous = await self._db.fetchval(
            "SELECT id FROM profit_share_terms WHERE valid_from=$1 ORDER BY id DESC LIMIT 1",
            valid_from,
        )
        await self._db.execute(
            """
            INSERT INTO profit_share_terms(valid_from,skyex_percent,pe_percent,created_by,operation_id,supersedes_id)
                VALUES ($1,$2,$3,$4,$5,$6)
        """,
            valid_from,
            skyex_percent,
            pe_percent,
            actor_id,
            operation_id,
            previous,
        )
        await self._db.execute(
            "INSERT INTO operation_audit_notes VALUES ($1,$2)", operation_id, reason
        )

    async def transferable(self, period):
        return [
            dict(row)
            for row in await self._db.fetch(
                """
            SELECT c.*,d.operation_id FROM best_change_calculations c JOIN deals d ON d.id=c.deal_id
                JOIN ledger_accounts recipient ON recipient.id=c.daniyar_wallet_account_id
                    AND recipient.purpose='best_change_buffer'
                JOIN active_financial_originals o ON o.id=d.operation_id
            WHERE o.business_date>=$1 AND o.business_date<($1::date+interval '1 month')
                AND NOT EXISTS(SELECT 1 FROM best_change_transfer_allocations a JOIN best_change_period_transfers t ON t.id=a.transfer_id
                    JOIN active_financial_originals x ON x.id=t.operation_id WHERE a.deal_id=c.deal_id)
            ORDER BY c.deal_id
        """,
                period,
            )
        ]

    async def prepare_close(self, period, revision_id):
        latest = await self._db.fetchrow(
            "SELECT c.*,EXISTS(SELECT 1 FROM active_financial_originals o WHERE o.id=c.operation_id) AS active FROM profit_period_closures c WHERE period=$1 ORDER BY revision DESC LIMIT 1",
            period,
        )
        if latest:
            if not revision_id or latest["active"]:
                raise InvalidPosting(
                    "Месяц уже закрыт: откройте ревизию и отмените прежнее закрытие"
                )
            if not await self._db.fetchval(
                "SELECT EXISTS(SELECT 1 FROM profit_period_revisions WHERE id=$1 AND period=$2 AND supersedes_id=$3)",
                revision_id,
                period,
                latest["id"],
            ):
                raise InvalidPosting("Ревизия не относится к предыдущему закрытию")
        elif revision_id:
            raise InvalidPosting("У месяца нет предыдущего закрытия")
        if await self._db.fetchval(
            "SELECT EXISTS(SELECT 1 FROM expense_period_revisions r LEFT JOIN expense_revision_completions c ON c.revision_id=r.id WHERE r.period=$1 AND c.revision_id IS NULL)",
            period,
        ):
            raise InvalidPosting("Сначала завершите ревизию выписки расходов")
        return dict(latest) if latest else {}

    async def close(
        self,
        operation_id,
        period,
        revision_id,
        report,
        term,
        sources,
        distributions,
        transfers,
        bindings,
    ):
        latest = await self.prepare_close(period, revision_id)
        closure = await self._db.fetchval(
            """
            INSERT INTO profit_period_closures(period,revision,operation_id,share_term_id,income_usdt,business_expenses_usdt,investor_interest_usdt,supersedes_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id
        """,
            period,
            latest.get("revision", 0) + 1,
            operation_id,
            term["id"],
            report["income_usdt"],
            report["business_expenses_usdt"],
            report["investor_interest_usdt"],
            latest.get("id"),
        )
        for row in sources:
            await self._db.execute(
                "INSERT INTO profit_closure_sources VALUES ($1,$2,$3,$4,$5)",
                closure,
                operation_id,
                row["operation_id"],
                row["account_id"],
                row["credit_signed_usdt"],
            )
        for party_id, amount in distributions:
            if amount:
                await self._db.execute(
                    "INSERT INTO partner_profit_accruals(closure_id,owner_party_id,amount_usdt) VALUES ($1,$2,$3)",
                    closure,
                    party_id,
                    amount,
                )
        total = sum(row["daniyar_share_usdt"] for row in transfers)
        if total:
            transfer = await self._db.fetchval(
                """
                INSERT INTO best_change_period_transfers(period,revision,operation_id,source_wallet_account_id,target_wallet_account_id,amount_usdt)
                VALUES ($1,$2,$3,$4,$5,$6) RETURNING id
            """,
                period,
                latest.get("revision", 0) + 1,
                operation_id,
                bindings["best_change_buffer"]["ledger_account_id"],
                bindings["daniyar_balance"]["ledger_account_id"],
                total,
            )
            for row in transfers:
                await self._db.execute(
                    "INSERT INTO best_change_transfer_allocations VALUES ($1,$2,$3)",
                    transfer,
                    row["deal_id"],
                    row["daniyar_share_usdt"],
                )
        if revision_id:
            await self._db.execute(
                "INSERT INTO profit_revision_completions VALUES ($1,$2)", revision_id, operation_id
            )
        else:
            await self._db.execute(
                "INSERT INTO period_locks(context,period,closed_by_operation_id) VALUES ('ledger',$1,$2),('profit',$1,$2)",
                period,
                operation_id,
            )
        return closure

    async def unpaid(self, recipient_id, platform, day):
        if platform:
            rows = await self._db.fetch(
                "SELECT * FROM platform_commission_balances WHERE remaining_usdt>0 AND business_date<date_trunc('month',$1::date) ORDER BY business_date,id",
                day,
            )
        else:
            rows = await self._db.fetch(
                "SELECT * FROM owner_profit_balances WHERE owner_party_id=$1 AND remaining_usdt>0 AND period<=$2 ORDER BY period,id",
                recipient_id,
                day,
            )
        return [dict(row) for row in rows]

    async def payment(
        self,
        operation_id,
        recipient_id,
        instrument,
        quantity,
        amount,
        quote,
        actor_id,
        effective_at,
        allocations,
        platform,
    ):
        rate = await self.rate(quote, actor_id, effective_at)
        payment = await self._db.fetchval(
            """
            INSERT INTO distribution_payments(recipient_party_id,operation_id,instrument_code,quantity,discharged_usdt,rate_snapshot_id)
                VALUES ($1,$2,$3,$4,$5,$6) RETURNING id
        """,
            recipient_id,
            operation_id,
            instrument,
            quantity,
            amount,
            rate,
        )
        for source_id, _source_operation_id, take in allocations:
            if platform:
                await self._db.execute(
                    "INSERT INTO platform_payment_allocations VALUES ($1,$2,$3,$4)",
                    payment,
                    source_id,
                    operation_id,
                    take,
                )
            else:
                await self._db.execute(
                    "INSERT INTO partner_profit_payment_allocations VALUES ($1,$2,$3)",
                    payment,
                    source_id,
                    take,
                )
        return payment

    async def start_revision(self, operation_id, period, reason):
        if not reason.strip():
            raise InvalidPosting("Причина ревизии обязательна")
        closure = await self._db.fetchval(
            "SELECT id FROM profit_period_closures WHERE period=$1 ORDER BY revision DESC LIMIT 1",
            period,
        )
        if closure is None:
            raise InvalidPosting("Пересмотр возможен только для закрытого месяца")
        return await self._db.fetchval(
            "INSERT INTO profit_period_revisions(period,operation_id,supersedes_id,reason) VALUES ($1,$2,$3,$4) RETURNING id",
            period,
            operation_id,
            closure,
            reason,
        )

    async def reversible(self, operation_id):
        if not await self._db.fetchval(
            """
            SELECT EXISTS(SELECT 1 FROM deals d JOIN best_change_calculations b ON b.deal_id=d.id WHERE d.operation_id=$1
                UNION ALL SELECT 1 FROM profit_period_closures WHERE operation_id=$1
                UNION ALL SELECT 1 FROM distribution_payments p WHERE p.operation_id=$1 AND
                    (EXISTS(SELECT 1 FROM partner_profit_payment_allocations WHERE payment_id=p.id)
                    OR EXISTS(SELECT 1 FROM platform_payment_allocations WHERE payment_id=p.id)))
        """,
            operation_id,
        ):
            raise ReversalBlocked("Нужен документ BestChange, закрытия прибыли или её выплаты")

    async def reversed(self, original_id, operation_id, reason):
        deal_id = await self._db.fetchval("SELECT id FROM deals WHERE operation_id=$1", original_id)
        if deal_id is not None:
            await self._db.execute(
                "INSERT INTO deal_reversals VALUES ($1,$2,$3,NULL)", deal_id, operation_id, reason
            )

    async def dependency_sources(self, ids):
        return [
            row["id"]
            for row in await self._db.fetch(
                "SELECT o.id FROM financial_operations o WHERE o.id=ANY($1::bigint[]) AND NOT EXISTS(SELECT 1 FROM financial_operations r WHERE r.reversal_of_id=o.id AND r.status='posted') ORDER BY o.id",
                sorted(ids),
            )
        ]


    async def lock_best_change(self, root_id):
        await self._db.execute("SELECT pg_advisory_xact_lock(hashtextextended('best-change:'||$1::bigint::text,0))",root_id)

    async def best_change_context(self, deal_id):
        root = await self._db.fetchval("""
            WITH RECURSIVE parents AS (
                SELECT deal_id,0 AS depth FROM best_change_calculations WHERE deal_id=$1
                UNION ALL SELECT r.original_deal_id,p.depth+1 FROM parents p
                    JOIN best_change_corrections r ON r.replacement_deal_id=p.deal_id
            ) SELECT deal_id FROM parents ORDER BY depth DESC LIMIT 1
        """,deal_id)
        if root is None:
            raise InvalidPosting('Сделка BestChange не найдена')
        row = await self._db.fetchrow("""
            WITH RECURSIVE chain AS (
                SELECT $1::bigint AS id,0 AS depth
                UNION ALL SELECT r.replacement_deal_id,c.depth+1 FROM chain c
                    JOIN best_change_corrections r ON r.original_deal_id=c.id
            ) SELECT b.*,d.operation_id,d.client_id,d.attributes,o.business_date,o.effective_at,
                EXISTS(SELECT 1 FROM active_financial_originals a WHERE a.id=d.operation_id) AS active
            FROM chain c JOIN deals d ON d.id=c.id JOIN best_change_calculations b ON b.deal_id=d.id
            JOIN financial_operations o ON o.id=d.operation_id ORDER BY c.depth DESC LIMIT 1
        """,root)
        import json
        return {'root_id':root,'active':{**dict(row),'attributes':json.loads(row['attributes'])}}
