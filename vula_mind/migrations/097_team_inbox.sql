-- ============================================================
-- Vula Group — Migration 097: team inbox essentials (assignment, notes, tags, canned replies)
-- (renumbered from 048 — that number collided with 048_inbox_handoff.sql)
-- Closes the biggest gap vs dedicated CS platforms (Cue): multi-agent handling. Run in Supabase.
-- ============================================================

-- 1. Per-conversation: who's handling it, an internal note, and tags for triage.
alter table commerce_conversation_sessions
    add column if not exists assigned_to text,          -- team member name/id handling it
    add column if not exists agent_note   text,          -- internal note (not sent to customer)
    add column if not exists tags         text[] not null default '{}';

create index if not exists idx_conv_sessions_assigned
    on commerce_conversation_sessions (tenant_id, assigned_to);

-- 2. Saved / canned replies the agent can insert with one tap.
create table if not exists commerce_canned_replies (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    title       text not null,          -- short label shown in the picker
    body        text not null,          -- the message text
    sort        int  not null default 0,
    created_at  timestamptz not null default now()
);
create index if not exists idx_canned_replies_tenant on commerce_canned_replies (tenant_id, sort);

alter table commerce_canned_replies enable row level security;
drop policy if exists "tenant_isolation" on commerce_canned_replies;
create policy "tenant_isolation" on commerce_canned_replies
    using (tenant_id = current_setting('app.tenant_id', true));

-- Off the Hook starter canned replies.
insert into commerce_canned_replies (tenant_id, title, body, sort) values
    ('off-the-hook', 'Delivery times', 'We deliver Tue–Sat. Morning slot 8–12, afternoon 12–5. Order by 9am for same-day where available. 🚚', 1),
    ('off-the-hook', 'Payment options', 'You can pay by card online, EFT, or cash/card on delivery — whichever suits you. 💳', 2),
    ('off-the-hook', 'Out of stock', 'Ah, that one''s sold out for today — fresh stock lands tomorrow. Can I suggest something similar? 🐟', 3),
    ('off-the-hook', 'Thanks / closing', 'Thanks so much for supporting Off the Hook! Shout any time you need fresh fish or chicken. 🎣', 4)
on conflict do nothing;
