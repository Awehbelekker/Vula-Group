-- ============================================================
-- Vula Group — Migration 042: Agent escalate-and-learn
-- When the agent isn't confident, it escalates the question to a human helper on WhatsApp,
-- relays their answer to the client, and remembers it so it can answer next time.
-- Run in Supabase SQL editor.
-- ============================================================

create table if not exists vula_escalations (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    customer_phone  text not null,
    question        text not null,
    status          text not null default 'open',   -- open | answered | closed
    helper_phone    text,
    helper_name     text,
    answer          text,
    channel         text default 'whatsapp',
    created_at      timestamptz not null default now(),
    answered_at     timestamptz
);
create index if not exists idx_escalations_helper on vula_escalations (helper_phone, status);
create index if not exists idx_escalations_tenant on vula_escalations (tenant_id, status);

create table if not exists vula_learned_answers (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    question    text not null,
    answer      text not null,
    source      text default 'escalation',
    created_at  timestamptz not null default now()
);
create index if not exists idx_learned_tenant on vula_learned_answers (tenant_id);

alter table vula_escalations enable row level security;
alter table vula_learned_answers enable row level security;
drop policy if exists "tenant_isolation" on vula_escalations;
drop policy if exists "tenant_isolation" on vula_learned_answers;
create policy "tenant_isolation" on vula_escalations using (tenant_id = current_setting('app.tenant_id', true));
create policy "tenant_isolation" on vula_learned_answers using (tenant_id = current_setting('app.tenant_id', true));
