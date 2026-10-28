-- 233_project_clients.sql — a project's client, on WhatsApp.
--
-- 28 Oct 2026 (Ian, DIGG: "can the client do everything we did like we went through
-- Belladonna?"). The tracker link (226) lets a client look; this lets them ask on the business's
-- WhatsApp line — what they've paid, what's due, the budget, what's left to do — answered only
-- from their own project, in the view the owner chose (vula/commerce/project_client.py). Their
-- slips for suppliers they paid themselves are held for the owner to approve, and a line they
-- question goes to the team. Nothing a client sends changes the books on its own.
--
--   phone       the client's WhatsApp number, digits only (27…)
--   view        price | open_book | qs — the same choice as a share link
--   revoked_at  the owner took the client off; the row stays as the record
--
-- vula_project_direct_payments.status gains 'proposed': a client's own slip waiting for the
-- owner. Only 'paid' and 'to_pay' count anywhere (project_tracker.direct_payments).
--
-- Read and written only by the API with the service key. RLS on, no policies. Idempotent.

create table if not exists vula_project_clients (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    project     text not null,
    phone       text not null,
    name        text,
    view        text not null default 'price' check (view in ('price', 'open_book', 'qs')),
    created_by  text,
    created_at  timestamptz not null default now(),
    revoked_at  timestamptz
);
create index if not exists idx_project_clients_phone on vula_project_clients (tenant_id, phone);
create unique index if not exists uq_project_clients_live
    on vula_project_clients (tenant_id, project, phone) where revoked_at is null;
alter table vula_project_clients enable row level security;

alter table vula_project_direct_payments drop constraint if exists vula_project_direct_payments_status_check;
alter table vula_project_direct_payments add constraint vula_project_direct_payments_status_check
    check (status in ('paid', 'to_pay', 'removed', 'proposed'));
