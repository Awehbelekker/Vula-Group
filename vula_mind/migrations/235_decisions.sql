-- 235_decisions.sql — the owner's rulings, remembered with their reason.
--
-- 31 Oct 2026 (Ian, DIGG): "start with the decision log". On Belladonna's final account the owner
-- settled a run of questions — Sea Point Hire is Belladonna, every Pudlo invoice was Belladonna
-- tiles, DIGG paid the R3,700 plumbing change, take out the extra tile labour, further painting is
-- billed separately. The books carry the resulting figures, but nothing carried WHY: asked "why is
-- the plumbing R9,700?", Vula could show the number and not the ruling behind it, and the next
-- month could repeat the mistakes the owner had already corrected.
--
-- One row per ruling, in plain words, with the project it is about and the rows it touched.
-- Distinct from vula_business_rules (152: a standing instruction applied to every answer) and
-- vula_learned_answers (150: a customer Q->A pair). A decision is a fact about what was settled,
-- read when the project comes up and when someone asks why a figure is what it is.
--
--   refs            [{"table": "commerce_expenses", "id": "…"}, …] — the rows the ruling changed
--   made_by_*       who settled it (an owner/manager on WhatsApp or the dashboard, or the operator)
--   status          active | superseded (a later ruling replaced it) | withdrawn (taken back)
--
-- Read and written only by the API with the service key. RLS on, no policies. Idempotent.

create table if not exists vula_decisions (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    project         text,
    subject         text,
    decision        text not null,
    amount_cents    bigint,
    refs            jsonb not null default '[]'::jsonb,
    made_by         text,
    made_by_role    text,
    made_by_phone   text,
    source          text not null default 'whatsapp'
                    check (source in ('whatsapp', 'dashboard', 'operator', 'system')),
    status          text not null default 'active'
                    check (status in ('active', 'superseded', 'withdrawn')),
    superseded_by   uuid,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists idx_decisions_project
    on vula_decisions (tenant_id, project, created_at desc) where status = 'active';
alter table vula_decisions enable row level security;
