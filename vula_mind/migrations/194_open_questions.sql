-- 194: the questions Vula has asked someone and is waiting on (2026-10-06). One row per question:
-- an approval, "which project is this document for?", "does this POP pay bill X?". A short reply
-- ("Approve", "Atlantis", "yes") is matched to that person's NEWEST open question first, instead
-- of a dozen handlers each guessing whether it was meant for them — which is how "Approve" to the
-- STE Scaffolding question approved a June test invoice, and "Atlantis Paarden Eiland" landed on
-- a Bauxite claim from August (vula/open_questions.py). Idempotent. RLS on (service role only).

create table if not exists vula_open_questions (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    text not null,
  phone        text not null,
  kind         text not null,          -- approval | doc_project | pop_match
  ref_id       text not null,          -- the approval / filed document / bank line it is about
  prompt       text,                   -- short preview of what was asked (no customer content)
  status       text not null default 'open',   -- open | answered | expired | closed
  answer       text,
  asked_at     timestamptz not null default now(),
  expires_at   timestamptz,
  answered_at  timestamptz
);

create index if not exists vula_open_questions_open_idx
  on vula_open_questions (tenant_id, phone, status, asked_at desc);
create index if not exists vula_open_questions_ref_idx
  on vula_open_questions (tenant_id, ref_id);

alter table vula_open_questions enable row level security;
