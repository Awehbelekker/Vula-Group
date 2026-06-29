-- 036_recurring_invoices.sql — recurring invoice templates that auto-generate on a cadence.
create table if not exists commerce_recurring_invoices (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    label           text,                              -- e.g. "Monthly retainer — Sporty"
    customer_name   text not null,
    customer_email  text, customer_phone text, customer_address text,
    line_items      jsonb not null default '[]'::jsonb,
    vat_rate        numeric not null default 15,
    cadence         text not null default 'monthly'    -- weekly | monthly
                        check (cadence in ('weekly', 'monthly')),
    next_run_at     date not null,
    active          boolean not null default true,
    last_invoice_id uuid,
    last_run_at     timestamptz,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists idx_recurring_due on commerce_recurring_invoices (tenant_id, active, next_run_at);
alter table commerce_recurring_invoices enable row level security;
drop policy if exists "tenant_isolation" on commerce_recurring_invoices;
create policy "tenant_isolation" on commerce_recurring_invoices
    using (tenant_id = current_setting('app.tenant_id', true));
