-- 213_website_leads.sql — sign-ups from the Vula website (vula-ai.com), 15 Oct 2026.
-- Each lead is stored here and sent to the Vula team on WhatsApp (and email when TEAM_EMAIL is
-- set), so nobody who asks for a demo is lost. Written only by the server; RLS on, no policies.
-- Idempotent; nothing dropped.

create table if not exists vula_website_leads (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    business    text not null,
    whatsapp    text not null,
    kind        text,                        -- what they do: shop / maker / construction / rep / services / other
    source      text,                        -- page or campaign the form was on
    status      text not null default 'new' check (status in ('new', 'contacted', 'won', 'lost')),
    ip          text,
    created_at  timestamptz not null default now()
);
create index if not exists idx_website_leads_created on vula_website_leads (created_at desc);
alter table vula_website_leads enable row level security;
