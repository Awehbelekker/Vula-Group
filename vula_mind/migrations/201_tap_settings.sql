-- ============================================================
-- Vula Group — Migration 201: per-tenant tap-to-pay switch + test-payment flag
-- Replaces the TAP_TO_PAY_TENANTS env allowlist for self-serve setup (the env list still works as
-- an operator override). mode: off -> testing (R5 test payment in flight) -> live. A tenant can only
-- go live after a test payment has actually been confirmed (tested_at).
-- kb_bills.is_test: the R5 setup test bill — skipped by the ledger and the staff alert.
-- Idempotent. Run in Supabase SQL editor.
-- ============================================================

create table if not exists kb_settings (
    tenant_id   text primary key,
    mode        text not null default 'off' check (mode in ('off', 'testing', 'live')),
    tested_at   timestamptz,
    updated_at  timestamptz not null default now()
);

alter table kb_settings enable row level security;
drop policy if exists "tenant_isolation" on kb_settings;
create policy "tenant_isolation" on kb_settings
    using (tenant_id = current_setting('app.tenant_id', true));

alter table kb_bills add column if not exists is_test boolean not null default false;
