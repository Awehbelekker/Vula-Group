-- ============================================================
-- Vula Group — Migration 121: double-entry general ledger (dual-write, internal use)
-- Per docs/general-ledger-proposal.md (sign-off received): commerce_bank_transactions /
-- commerce_expenses are single-entry categorised records — fine for bank-rec/VAT/P&L narrative,
-- but Vula can't produce a trial balance or balance sheet from them. This adds a genuine
-- double-entry layer that DUAL-WRITES alongside those tables (they are unchanged and keep
-- working exactly as before) for new transactions only, posted in real time from the moment an
-- order/invoice is paid or an expense is logged. No historical backfill.
--
-- unique(tenant_id, source_type, source_id) on journal_entries is the idempotency mechanism —
-- an order can be marked "paid" from 3 independent code paths (Yoco webhook, bank-rec
-- auto-match, manual admin review), all funnelling through the same two service.py functions,
-- so the SECOND attempt to post the same event must be a safe no-op, not a duplicate entry.
--
-- journal_lines has no tenant_id of its own — RLS is join-scoped through journal_entry_id,
-- exactly the pattern in 114_rls_join_scoped_tables.sql.
-- Run in Supabase SQL editor.
-- ============================================================

create table if not exists journal_entries (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    entry_date   date not null,
    description  text not null,
    source_type  text not null,   -- 'order_paid' | 'invoice_paid' | 'expense' | 'order_refund' | 'manual'
    source_id    text,            -- the originating row's id, when there is one (null for manual entries)
    created_at   timestamptz not null default now(),
    unique (tenant_id, source_type, source_id)
);
create index if not exists idx_journal_entries_tenant on journal_entries (tenant_id, entry_date desc);

alter table journal_entries enable row level security;
drop policy if exists "tenant_isolation" on journal_entries;
create policy "tenant_isolation" on journal_entries
    using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists journal_lines (
    id                uuid primary key default gen_random_uuid(),
    journal_entry_id  uuid not null references journal_entries(id) on delete cascade,
    account_id        uuid not null references commerce_accounts(id),
    debit_cents       bigint not null default 0,
    credit_cents      bigint not null default 0,
    memo              text,
    check (debit_cents = 0 or credit_cents = 0),      -- exactly one side per line
    check (debit_cents >= 0 and credit_cents >= 0)
);
create index if not exists idx_journal_lines_entry on journal_lines (journal_entry_id);
create index if not exists idx_journal_lines_account on journal_lines (account_id);

alter table journal_lines enable row level security;
drop policy if exists "tenant_isolation" on journal_lines;
create policy "tenant_isolation" on journal_lines
    using (exists (
        select 1 from journal_entries je
        where je.id = journal_lines.journal_entry_id
          and je.tenant_id = current_setting('app.tenant_id', true)
    ));

-- ── Posting RPC (atomic: entry + all lines in one transaction, balance-checked) ──────────────
-- p_lines shape: [{"account_code": "sales", "debit_cents": 0, "credit_cents": 15000, "memo": ""}, ...]
create or replace function post_journal_entry(
    p_tenant_id text,
    p_entry_date date,
    p_description text,
    p_source_type text,
    p_source_id text,
    p_lines jsonb
) returns uuid as $$
declare
    v_entry_id    uuid;
    v_debit_sum   bigint;
    v_credit_sum  bigint;
    v_line        jsonb;
    v_account_id  uuid;
begin
    select coalesce(sum((l->>'debit_cents')::bigint), 0), coalesce(sum((l->>'credit_cents')::bigint), 0)
        into v_debit_sum, v_credit_sum
        from jsonb_array_elements(p_lines) as l;

    if v_debit_sum != v_credit_sum then
        raise exception 'unbalanced journal entry: debits % != credits %', v_debit_sum, v_credit_sum;
    end if;
    if v_debit_sum = 0 then
        raise exception 'journal entry has zero value';
    end if;

    insert into journal_entries (tenant_id, entry_date, description, source_type, source_id)
        values (p_tenant_id, p_entry_date, p_description, p_source_type, p_source_id)
        on conflict (tenant_id, source_type, source_id) do nothing
        returning id into v_entry_id;

    if v_entry_id is null then
        -- Either a genuine race (another caller posted the same source_type+source_id first —
        -- return its id so this call is a safe no-op) or source_id was null (never conflicts,
        -- so null here means the insert itself failed for another reason).
        if p_source_id is not null then
            select id into v_entry_id from journal_entries
                where tenant_id = p_tenant_id and source_type = p_source_type and source_id = p_source_id;
        end if;
        if v_entry_id is not null then
            return v_entry_id;
        end if;
        raise exception 'journal entry insert failed unexpectedly';
    end if;

    for v_line in select * from jsonb_array_elements(p_lines) loop
        select id into v_account_id from commerce_accounts
            where tenant_id = p_tenant_id and code = (v_line->>'account_code');
        if v_account_id is null then
            raise exception 'unknown account code % for tenant %', v_line->>'account_code', p_tenant_id;
        end if;
        insert into journal_lines (journal_entry_id, account_id, debit_cents, credit_cents, memo)
            values (v_entry_id, v_account_id,
                    coalesce((v_line->>'debit_cents')::bigint, 0),
                    coalesce((v_line->>'credit_cents')::bigint, 0),
                    v_line->>'memo');
    end loop;

    return v_entry_id;
end;
$$ language plpgsql;
