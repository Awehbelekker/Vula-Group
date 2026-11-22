-- _APPLY_2026-11-17_286_288.sql — paste into the Supabase SQL editor and run once.
-- Migrations 286 (project client emails, PR #336) and 288 (ledger v2 groundwork), each recorded in
-- vula_schema_migrations. One transaction: all of it applies, or none of it. Idempotent.
begin;

-- ── 286 ──
-- 286_project_client_emails.sql — emails sent to a project's client from the business's own mailbox.
--
-- 16 Nov 2026 (Ian, DIGG): "send him approval so that we can prove he has access". The owner writes
-- the email on the project's Money tab and confirms; Vula sends it from the business's connected
-- mailbox, sends a copy to that mailbox, and keeps this row: who sent it, to whom, when, the subject
-- and the text, and whether it went (vula/commerce/project_emails.py).
-- Idempotent.

create table if not exists vula_project_client_emails (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    project     text not null,
    recipients  text[] not null default '{}',
    subject     text not null,
    body        text not null,
    sent_by     text,
    status      text not null default 'sending' check (status in ('sending', 'sent', 'failed')),
    error       text,
    copy_to     text,
    sent_at     timestamptz,
    created_at  timestamptz not null default now()
);

create index if not exists vula_project_client_emails_tenant_project
    on vula_project_client_emails (tenant_id, project, created_at desc);

alter table vula_project_client_emails enable row level security;

-- ── 288 ──
-- 288_ledger_v2.sql — one book you can trust (Phase 1 of Xero-quality books, groundwork).
--
-- 17 Nov 2026 (Ian: "finish Vula's own books first"). The journal (migration 121) becomes the one
-- source of truth, kept on an accrual basis with a cash view of the same entries, like Xero. This
-- migration only adds what that needs; nothing posts the new way until a business's `books_v2`
-- switch is on (vula/features.py), and a business's history is rebuilt only after its books audit
-- is signed off.
--
--   journal_lines.basis           null = both views; 'accrual' or 'cash' = that view only. Each view
--                                 balances on its own (an invoice receipt: Dr bank (both) /
--                                 Cr debtors (accrual) / Cr sales + VAT (cash)).
--   journal_lines.bank_account_id which bank account a bank line moved (reconciliation per account)
--   journal_entries.version       a source can be corrected: the live entry is reversed and the
--                                 source posted again as the next version (the journal stays
--                                 append-only, migration 253). The unique key becomes
--                                 (tenant, source_type, source_id, version); post_journal_entry
--                                 is replaced in this same migration to match, so every existing
--                                 caller keeps working (it posts version 1, as before).
--   journal_entries.reverses_entry_id  a reversal names the entry it reverses (at most once)
--   journal_entries.doc_id / bank_txn_id  the document and bank line behind an entry
--   journal_bank_links            each bank line owns exactly one cash leg (no double counting)
--   commerce_books_settings       per business: accrual from, VAT basis, year end, lock date, …
--   journal_post_failures         a posting that failed, kept to retry and to show — never silent
--   post_journal_entry_v2         balances each view, one live entry per source, takes the meta
--   ledger_balances               sums per account (and bank account) for one view, in the database
-- Idempotent.

alter table journal_entries add column if not exists version int not null default 1;
alter table journal_entries add column if not exists reverses_entry_id uuid references journal_entries(id);
alter table journal_entries add column if not exists doc_id text;
alter table journal_entries add column if not exists bank_txn_id uuid;

alter table journal_lines add column if not exists basis text;
alter table journal_lines add column if not exists bank_account_id uuid;
do $$ begin
    if not exists (select 1 from pg_constraint where conname = 'journal_lines_basis_check') then
        alter table journal_lines add constraint journal_lines_basis_check
            check (basis is null or basis in ('accrual', 'cash'));
    end if;
end $$;

-- the unique key moves to include the version (a corrected source posts version 2, 3, …)
create unique index if not exists journal_entries_source_version
    on journal_entries (tenant_id, source_type, source_id, version);
do $$ begin
    if exists (select 1 from pg_constraint where conname = 'journal_entries_tenant_id_source_type_source_id_key') then
        alter table journal_entries drop constraint journal_entries_tenant_id_source_type_source_id_key;
    end if;
end $$;
create unique index if not exists journal_entries_reversed_once
    on journal_entries (reverses_entry_id) where reverses_entry_id is not null;
create index if not exists idx_journal_entries_bank_txn on journal_entries (tenant_id, bank_txn_id)
    where bank_txn_id is not null;
create index if not exists idx_journal_lines_bank_account on journal_lines (bank_account_id)
    where bank_account_id is not null;

-- the existing RPC, unchanged except for the conflict target (version 1, as every caller posts today)
create or replace function post_journal_entry(
    p_tenant_id text,
    p_entry_date date,
    p_description text,
    p_source_type text,
    p_source_id text,
    p_lines jsonb
) returns uuid language plpgsql set search_path = public, pg_temp as $$
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
        on conflict (tenant_id, source_type, source_id, version) do nothing
        returning id into v_entry_id;

    if v_entry_id is null then
        if p_source_id is not null then
            select id into v_entry_id from journal_entries
                where tenant_id = p_tenant_id and source_type = p_source_type and source_id = p_source_id
                order by version limit 1;
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
end $$;

create or replace function post_journal_entry_v2(
    p_tenant_id text,
    p_entry_date date,
    p_description text,
    p_source_type text,
    p_source_id text,
    p_lines jsonb,
    p_meta jsonb default '{}'::jsonb
) returns uuid language plpgsql set search_path = public, pg_temp as $$
declare
    v_entry_id   uuid;
    v_version    int := 1;
    v_reverses   uuid := nullif(p_meta->>'reverses_entry_id', '')::uuid;
    v_line       jsonb;
    v_account_id uuid;
    a_dr bigint; a_cr bigint; c_dr bigint; c_cr bigint;
begin
    select coalesce(sum((l->>'debit_cents')::bigint)  filter (where coalesce(l->>'basis', '') in ('', 'accrual')), 0),
           coalesce(sum((l->>'credit_cents')::bigint) filter (where coalesce(l->>'basis', '') in ('', 'accrual')), 0),
           coalesce(sum((l->>'debit_cents')::bigint)  filter (where coalesce(l->>'basis', '') in ('', 'cash')), 0),
           coalesce(sum((l->>'credit_cents')::bigint) filter (where coalesce(l->>'basis', '') in ('', 'cash')), 0)
      into a_dr, a_cr, c_dr, c_cr
      from jsonb_array_elements(p_lines) as l;
    if a_dr != a_cr then
        raise exception 'unbalanced journal entry (accrual view): debits % != credits %', a_dr, a_cr;
    end if;
    if c_dr != c_cr then
        raise exception 'unbalanced journal entry (cash view): debits % != credits %', c_dr, c_cr;
    end if;
    if a_dr = 0 and c_dr = 0 then
        raise exception 'journal entry has zero value';
    end if;

    -- one live entry per source: the same call twice is a no-op
    perform pg_advisory_xact_lock(hashtext(p_tenant_id || '|' || p_source_type || '|' || coalesce(p_source_id, '')));
    if p_source_id is not null then
        select je.id into v_entry_id from journal_entries je
         where je.tenant_id = p_tenant_id and je.source_type = p_source_type and je.source_id = p_source_id
           and not exists (select 1 from journal_entries r where r.reverses_entry_id = je.id)
         order by je.version desc limit 1;
        if v_entry_id is not null then
            return v_entry_id;
        end if;
        select coalesce(max(version), 0) + 1 into v_version from journal_entries
         where tenant_id = p_tenant_id and source_type = p_source_type and source_id = p_source_id;
    end if;
    if v_reverses is not null and not exists (
            select 1 from journal_entries where id = v_reverses and tenant_id = p_tenant_id) then
        raise exception 'the entry to reverse is not this business''s';
    end if;

    insert into journal_entries (tenant_id, entry_date, description, source_type, source_id, version,
                                 reverses_entry_id, doc_id, bank_txn_id)
        values (p_tenant_id, p_entry_date, p_description, p_source_type, p_source_id, v_version,
                v_reverses, nullif(p_meta->>'doc_id', ''), nullif(p_meta->>'bank_txn_id', '')::uuid)
        returning id into v_entry_id;

    for v_line in select * from jsonb_array_elements(p_lines) loop
        select id into v_account_id from commerce_accounts
            where tenant_id = p_tenant_id and code = (v_line->>'account_code');
        if v_account_id is null then
            raise exception 'unknown account code % for tenant %', v_line->>'account_code', p_tenant_id;
        end if;
        insert into journal_lines (journal_entry_id, account_id, debit_cents, credit_cents, memo, basis, bank_account_id)
            values (v_entry_id, v_account_id,
                    coalesce((v_line->>'debit_cents')::bigint, 0),
                    coalesce((v_line->>'credit_cents')::bigint, 0),
                    v_line->>'memo', nullif(v_line->>'basis', ''),
                    nullif(v_line->>'bank_account_id', '')::uuid);
    end loop;
    return v_entry_id;
end $$;

create or replace function ledger_balances(
    p_tenant_id text,
    p_from date default null,
    p_to date default null,
    p_basis text default 'accrual'
) returns table (account_code text, account_name text, account_type text,
                 debit_cents bigint, credit_cents bigint, bank_account_id uuid)
language sql stable set search_path = public, pg_temp as $$
    select a.code, a.name, a.type,
           coalesce(sum(l.debit_cents), 0)::bigint, coalesce(sum(l.credit_cents), 0)::bigint, l.bank_account_id
      from journal_lines l
      join journal_entries e on e.id = l.journal_entry_id
      join commerce_accounts a on a.id = l.account_id
     where e.tenant_id = p_tenant_id
       and (p_from is null or e.entry_date >= p_from)
       and (p_to is null or e.entry_date <= p_to)
       and (l.basis is null or l.basis = p_basis)
     group by a.code, a.name, a.type, l.bank_account_id
$$;

-- only the server (service role) calls these
revoke execute on function post_journal_entry_v2(text, date, text, text, text, jsonb, jsonb) from public, anon, authenticated;
revoke execute on function ledger_balances(text, date, date, text) from public, anon, authenticated;
grant execute on function post_journal_entry_v2(text, date, text, text, text, jsonb, jsonb) to service_role;
grant execute on function ledger_balances(text, date, date, text) to service_role;

create table if not exists journal_bank_links (
    id                uuid primary key default gen_random_uuid(),
    tenant_id         text not null,
    bank_txn_id       uuid not null,
    journal_entry_id  uuid not null references journal_entries(id),
    role              text not null default 'cash',
    created_at        timestamptz not null default now(),
    unique (tenant_id, bank_txn_id)
);
alter table journal_bank_links enable row level security;
drop policy if exists "tenant_isolation" on journal_bank_links;
create policy "tenant_isolation" on journal_bank_links using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists commerce_books_settings (
    tenant_id            text primary key,
    accrual_from         date,
    vat_basis            text not null default 'invoice' check (vat_basis in ('invoice', 'payments')),
    vat_registered_from  date,
    year_end_month       int not null default 2 check (year_end_month between 1 and 12),
    lock_date            date,
    backfill_report      jsonb,
    updated_at           timestamptz not null default now()
);
alter table commerce_books_settings enable row level security;
drop policy if exists "tenant_isolation" on commerce_books_settings;
create policy "tenant_isolation" on commerce_books_settings using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists journal_post_failures (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    source_type  text not null,
    source_id    text,
    payload      jsonb not null,
    error        text,
    attempts     int not null default 1,
    resolved_at  timestamptz,
    created_at   timestamptz not null default now()
);
create index if not exists journal_post_failures_open on journal_post_failures (tenant_id, created_at desc)
    where resolved_at is null;
alter table journal_post_failures enable row level security;
drop policy if exists "tenant_isolation" on journal_post_failures;
create policy "tenant_isolation" on journal_post_failures using (tenant_id = current_setting('app.tenant_id', true));

insert into vula_schema_migrations (filename, number, sha256, applied_by) values
  ('286_project_client_emails.sql', 286, '97f38338c1e8cfd2d05295a6290f73134dae27ca04ca8662beb616bd0cd55682', 'Ian via SQL editor (prepared by claude-code 01G4LT7c)'),
  ('288_ledger_v2.sql', 288, 'd979fc2de527ab77006ac46223965bfa6f4451321ec6f8c23ad5e873a95b4cb5', 'Ian via SQL editor (prepared by claude-code 01G4LT7c)')
on conflict do nothing;
commit;
