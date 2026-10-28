-- ============================================================
-- Vula Group — Migration 232: Tap to Pay audit trail
-- Who did what to money on the tap side: an owner or a staff phone creating, cancelling, releasing
-- or writing off a bill, adding or removing a drink on a tab, replacing a tag, signing a phone in or
-- out, changing shares or switching tabs/Tap to Pay on and off (vula/tap/audit.py).
-- APPEND-ONLY (trigger below, like kb_ledger_lines): rows can't be changed or deleted.
-- No customer phone numbers are stored here.
-- Idempotent. Run in the Supabase SQL editor.
-- ============================================================
create table if not exists kb_audit (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    actor_kind  text not null check (actor_kind in ('owner','staff','customer','system')),
    actor       text not null default '',             -- owner email / team member id
    action      text not null,
    bill_id     uuid,
    detail      jsonb not null default '{}'::jsonb,
    created_at  timestamptz not null default now()
);
create index if not exists idx_kb_audit_tenant_time on kb_audit (tenant_id, created_at desc);
create index if not exists idx_kb_audit_bill on kb_audit (tenant_id, bill_id) where bill_id is not null;

create or replace function kb_audit_append_only() returns trigger language plpgsql as $$
begin
    raise exception 'kb_audit is append-only';
end $$;
drop trigger if exists trg_kb_audit_append_only on kb_audit;
create trigger trg_kb_audit_append_only before update or delete on kb_audit
    for each row execute function kb_audit_append_only();

-- server-side access only, like every other kb_* table
alter table kb_audit enable row level security;
