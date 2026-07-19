-- Vula Group — Migration 063: WhatsApp template registry (local tenant grouping)
-- Run in: Supabase Dashboard > SQL Editor > New Query > Run
-- ============================================================================
-- Meta's WhatsApp Business Account is the SOURCE OF TRUTH for template approval status — this
-- table never caches status (it goes stale the moment Meta reviews). It exists purely so Vula's
-- dashboard can show "which templates belong to which tenant" and remember the body text/params
-- used to create each one (Meta's own template list API doesn't group by Vula tenant).

create table if not exists commerce_wa_templates (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    name         text not null,             -- must match the name submitted to Meta, globally unique per WABA
    category     text not null default 'UTILITY',
    language     text not null default 'en',
    body_text    text not null,             -- with {{1}}, {{2}}... placeholders
    param_count  integer not null default 0,
    example      jsonb,                     -- example values submitted for each placeholder
    created_by   text,                      -- phone/name of who created it, if known
    created_at   timestamptz not null default now(),
    unique (tenant_id, name)
);
create index if not exists idx_wa_templates_tenant on commerce_wa_templates (tenant_id);
alter table commerce_wa_templates enable row level security;
drop policy if exists "tenant_isolation" on commerce_wa_templates;
create policy "tenant_isolation" on commerce_wa_templates
    using (tenant_id = current_setting('app.tenant_id', true));
