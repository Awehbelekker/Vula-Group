-- 176_reply_feedback.sql — owner/staff ratings and corrections of the assistant's replies.
--
-- The Inbox shows every AI reply; a 👍/👎 (and an optional "what it should have said") is the
-- cheapest real signal of quality there is. A correction is also taught to the assistant
-- (same path as admin/agent-teach), and 👎 rows are the raw material for new eval cases
-- (vula_mind/evals). Stores only what the business already sees in its own Inbox.
-- Idempotent.

create table if not exists vula_reply_feedback (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    session_id   text not null,
    message_id   text not null,
    rating       text not null check (rating in ('up', 'down')),
    question     text,
    answer       text,
    correction   text,
    created_at   timestamptz not null default now(),
    unique (tenant_id, message_id)
);

create index if not exists idx_reply_feedback_tenant on vula_reply_feedback (tenant_id, created_at desc);

alter table vula_reply_feedback enable row level security;
drop policy if exists "tenant_isolation" on vula_reply_feedback;
create policy "tenant_isolation" on vula_reply_feedback
    using (tenant_id = current_setting('app.tenant_id', true));
