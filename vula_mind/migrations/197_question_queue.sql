-- 197: questions wait their turn (2026-10-06, step 2 of the chat rework). A question nobody
-- prompted — "which project?" for a document that came in by email, an approval someone else
-- asked for — no longer lands on top of one the person is still answering. While they have an
-- open question less than 2 hours old, the new one is stored with status 'queued' and its full
-- WhatsApp text, and is sent when they answer (or after 2 hours unanswered, by the 5-minute
-- sweep). vula/open_questions.ask_or_queue / release_next. Idempotent; the table already has
-- RLS on (migration 194).

alter table vula_open_questions add column if not exists message text;

create index if not exists vula_open_questions_queued_idx
  on vula_open_questions (status, tenant_id, phone, asked_at) where status = 'queued';
