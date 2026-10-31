-- 239_forms.sql — forms the team fills in, and the time worked they record.
--
-- 31 Oct 2026 (Ian: "plan item 2"; all four forms, read back and staged for approval). Vula makes a
-- quote/order form, a site checklist, a timesheet or a stock count as a spreadsheet (vula/commerce/
-- forms.py): the business's logo, dropdowns from its real lists, the maths built in. When it comes
-- back on WhatsApp, the filled cells are read, checked and shown to the owner; nothing is booked
-- until they confirm.
--
--   vula_forms              one row per form handed out: its type, the project, the row map and
--                           the lists it was made with (spec), what was read back (staged) and
--                           its status — sent → returned → booked (or cancelled). A returned file
--                           is matched by the id on its hidden sheet AND the business.
--   vula_timesheet_entries  time worked from a confirmed timesheet: who, where, when, how much
--                           and the pay due. A record of time and pay owed, not a cost — the cost
--                           is the bank payment, so nothing is counted twice.
--
-- Read and written only by the API with the service key. RLS on, no policies. Idempotent.

create table if not exists vula_forms (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    form_type     text not null check (form_type in ('quote', 'site_checklist', 'timesheet', 'stock_count')),
    project       text,
    spec          jsonb not null default '{}'::jsonb,
    staged        jsonb,
    status        text not null default 'sent' check (status in ('sent', 'returned', 'booked', 'cancelled')),
    created_by    text,
    returned_by   text,
    booked_by     text,
    created_at    timestamptz not null default now(),
    returned_at   timestamptz,
    booked_at     timestamptz
);
create index if not exists idx_forms_tenant on vula_forms (tenant_id, created_at desc);
alter table vula_forms enable row level security;

create table if not exists vula_timesheet_entries (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    form_id       uuid,
    worker_id     uuid,
    worker_name   text not null,
    project       text,
    work_date     date not null,
    quantity      numeric not null check (quantity > 0),
    unit          text not null default 'day' check (unit in ('day', 'week', 'hour')),
    rate_cents    bigint,
    total_cents   bigint,
    created_by    text,
    created_at    timestamptz not null default now()
);
create index if not exists idx_timesheet_tenant on vula_timesheet_entries (tenant_id, work_date);
alter table vula_timesheet_entries enable row level security;
