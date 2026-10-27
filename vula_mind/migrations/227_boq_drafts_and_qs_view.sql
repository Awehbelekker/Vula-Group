-- 227_boq_drafts_and_qs_view.sql — a BOQ priced online, and a QS's view of a project.
--
-- 27 Oct 2026 (Ian: "can the tenant just do the BOQ online if needed and not download, also can
-- you share access to the QS and give a quick view dashboard on what a QS would like to view").
--
-- vula_boq_drafts: one working BOQ per project, priced in the dashboard (vula/commerce/
-- boq_online.py). Each line: section (ASAQS trade), library key, description, unit, qty,
-- rate_cents, area — money in integer cents, line amounts worked out by the server. "Use as the
-- project budget" writes its total and section totals to vula_project_boq, unless that project's
-- baseline is signed and locked (project_programme.set_baseline).
--
-- vula_project_shares.view gains 'qs': a quantity surveyor's read-only view of the project —
-- contract, certified and anticipated per bill, variations, valuations, spend against the
-- budget, programme, and the document register — never the business's fee, overheads or profit.
--
-- RLS on, no policies (service key only), like migration 226. Idempotent; run after 226.

create table if not exists vula_boq_drafts (
    tenant_id   text not null,
    project     text not null,
    lines       jsonb not null default '[]'::jsonb,
    total_cents bigint not null default 0,
    updated_by  text,
    updated_at  timestamptz not null default now(),
    primary key (tenant_id, project)
);
alter table vula_boq_drafts enable row level security;

alter table vula_project_shares drop constraint if exists vula_project_shares_view_check;
alter table vula_project_shares add constraint vula_project_shares_view_check
    check (view in ('price', 'open_book', 'qs'));
