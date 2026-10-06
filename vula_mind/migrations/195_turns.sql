-- 195: one row per WhatsApp message Vula handled — what came in, what it did and what it sent
-- (2026-10-06, step 1 of the chat rework). Until now a reply about a document never reached the
-- chat history and outbound messages kept a 200-character preview, so "why did Vula answer
-- that?" took an hour of log-reading. Each row holds the inbound text (or filename), the steps
-- taken (open question answered, skill, tools called, model route, errors) and every reply sent,
-- in full (vula/turns.py). Same content the conversation tables already hold; RLS on (service
-- role only). Idempotent.

create table if not exists vula_turns (
  id           uuid primary key,
  tenant_id    text,
  phone        text not null,
  wamid        text,
  kind         text not null,                 -- text | voice | document | image | button | location
  text         text,
  steps        jsonb not null default '[]'::jsonb,
  replies      jsonb not null default '[]'::jsonb,
  outcome      text,                          -- done | failed
  started_at   timestamptz not null default now(),
  duration_ms  integer
);

create index if not exists vula_turns_phone_idx on vula_turns (tenant_id, phone, started_at desc);

alter table vula_turns enable row level security;
