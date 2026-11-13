-- 269_research_notes.sql — what Vula found when it researched something, kept so it isn't
-- researched again.
--
-- 13 Nov 2026 (Ian: "how can we ensure Vula learns from interactions so we have less research").
-- Research mode (vula/research.py) threw every answer away after showing it, so the same question
-- the next day — or from a colleague — searched the web and read the pages again. A finished,
-- cited answer is kept here (vula/research_memory.py) and reused for a similar question, shown
-- with its date and a "Research again" button.
--
--   kind        'prices' (prices, stock, suppliers: reused 30 days) or 'general' (180 days)
--   shared      true = the business's whole team may reuse it; false = only the person who asked
--   created_by  the person's key (m:<team member id> / u:<login id>), never a phone number
--   superseded_by  a newer note on the same question, after "Research again"
--
-- The question is indexed in the business's knowledge base (source_type research_note, staff
-- audience) only to find it; this row is the source of truth. Deleted by "delete my data"
-- (vula/privacy_data.py). Read and written only by the API with the service key. RLS on, no
-- policies. Idempotent.

create table if not exists vula_research_notes (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    question        text not null,
    answer          text not null,
    sources         jsonb not null default '[]'::jsonb,
    documents       jsonb not null default '[]'::jsonb,
    searched        jsonb not null default '[]'::jsonb,
    kind            text not null default 'general' check (kind in ('prices', 'general')),
    shared          boolean not null default true,
    created_by      text,
    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null,
    reused_count    integer not null default 0,
    last_reused_at  timestamptz,
    superseded_by   uuid
);
create index if not exists idx_research_notes_tenant on vula_research_notes (tenant_id, expires_at);
alter table vula_research_notes enable row level security;
