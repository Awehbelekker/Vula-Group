-- 264_personal_memory.sql — what a staff member asked Vula to remember about them, private to them.
--
-- 12 Nov 2026 (Ian): staff talk to Vula for advice, planning and brainstorming, and some of what
-- they'd like it to remember is personal. Until now "remember…" could only become a company-wide
-- rule (152) that every colleague's assistant read. The owner's decisions: a note is private to
-- the person who gave it — not visible to colleagues, the owner or the operator — and is saved
-- only when they ask (vula/personal_memory.py).
--
--   member_id   the team member it belongs to (vula_team_members.id), or
--   user_id     the dashboard login, for an owner with no team row. Exactly one is set.
--   content     the note, Fernet-encrypted ("fernet:…", vula/secret_keys.py) like saved credentials
--   source      whatsapp | dashboard — where they asked
--
-- Deleted when the person deletes it, asks for their data to be deleted, or is removed from the
-- team. There is deliberately NO tenant-wide policy here: with one, a colleague's own login could
-- read these rows through the database API. RLS on, no policies; only the service key reads them,
-- and the code only ever reads the caller's own. Idempotent.

create table if not exists vula_personal_memory (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    member_id   uuid,
    user_id     uuid,
    content     text not null,
    source      text not null default 'whatsapp',
    created_at  timestamptz not null default now(),
    check ((member_id is not null) <> (user_id is not null))
);
create index if not exists idx_personal_memory_member
    on vula_personal_memory (tenant_id, member_id, created_at);
create index if not exists idx_personal_memory_user
    on vula_personal_memory (tenant_id, user_id, created_at);
alter table vula_personal_memory enable row level security;
