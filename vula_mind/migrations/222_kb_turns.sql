-- 222_kb_turns.sql — what happened to each staff question about the business's documents
-- (24 Oct 2026, Ian: "I just want it to be accurate, and if it can't, ask the right question to
-- get to the right answer ... so over time it gets better, but to learn we need proper data").
--
-- One row per staff/owner knowledge question (never a customer's): which documents the search
-- found, whether Vula answered, asked which document was meant, or found nothing; which option
-- the person picked; and whether they then said the answer was wrong. A pick is a confirmed
-- question -> document pair, the learning signal (vula/kb_learning.py).
--
-- `asker` is a hash of tenant + phone, never the number. `question` is the staff member's own
-- wording; `candidates` / `answer_docs` hold document ids and labels, never document text.
-- The server (service role) is the only reader/writer; RLS on with no policies. Idempotent.

create table if not exists vula_kb_turns (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    asker        text not null,
    role         text,
    channel      text not null default 'whatsapp',
    question     text not null,
    candidates   jsonb not null default '[]'::jsonb,   -- [{doc_id, label}] the search's top documents
    outcome      text not null check (outcome in ('answered', 'clarify', 'no_doc')),
    skill        text,
    answer_docs  jsonb not null default '[]'::jsonb,   -- [doc_id] the answer was given from
    picked_doc   text,                                 -- the option chosen after a 'clarify'
    picked_at    timestamptz,
    feedback     text check (feedback in ('confirmed', 'corrected')),
    feedback_at  timestamptz,
    created_at   timestamptz not null default now()
);
create index if not exists idx_kb_turns_asker on vula_kb_turns (tenant_id, asker, created_at desc);
create index if not exists idx_kb_turns_tenant on vula_kb_turns (tenant_id, created_at desc);
alter table vula_kb_turns enable row level security;

comment on table vula_kb_turns is
    'Staff knowledge questions: found documents, answered / asked which / nothing, the pick, feedback';
