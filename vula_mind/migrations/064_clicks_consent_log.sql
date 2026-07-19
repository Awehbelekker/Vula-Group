-- Vula Group — Migration 064: broadcast click tracking + consent audit trail
-- Run in: Supabase Dashboard > SQL Editor > New Query > Run
-- ============================================================================

-- Click tracking extends the EXISTING per-recipient row (commerce_broadcast_recipients, migration
-- 020) rather than a new table — that row is already keyed by wamid and updated by the delivery
-- status webhook, so a click is just one more fact about the same send.
alter table commerce_broadcast_recipients
    add column if not exists short_code text,          -- the /l/{code} token embedded in this send
    add column if not exists clicked_at  timestamptz,   -- first click time, null until clicked
    add column if not exists click_count integer not null default 0,
    add column if not exists target_url  text;          -- the real URL the short link points to

create unique index if not exists idx_bcast_recipients_short_code
    on commerce_broadcast_recipients (short_code) where short_code is not null;

-- Rolled-up click count on the broadcast log itself, same pattern as delivered_count/read_count
-- (record_message_status) — the dashboard reads this one column rather than aggregating recipients.
alter table commerce_broadcast_logs add column if not exists clicked_count int default 0;

-- Consent AUDIT TRAIL — append-only log of every consent state change, distinct from
-- commerce_consent (migration 020, current-state table used for suppression checks). POPIA
-- enforcement wants proof of when/how/why consent changed, not just the current state.
create table if not exists commerce_consent_log (
    id         uuid primary key default gen_random_uuid(),
    tenant_id  text not null,
    phone      text not null,
    action     text not null,              -- opted_in | opted_out
    source     text,                       -- inbound | stop_keyword | dashboard | onboarding_stop | ...
    purpose    text default 'marketing',   -- consent CATEGORY this event applies to
    created_at timestamptz not null default now()
);
create index if not exists idx_consent_log_tenant_phone
    on commerce_consent_log (tenant_id, phone, created_at desc);
alter table commerce_consent_log enable row level security;
drop policy if exists "tenant_isolation" on commerce_consent_log;
create policy "tenant_isolation" on commerce_consent_log
    using (tenant_id = current_setting('app.tenant_id', true));
