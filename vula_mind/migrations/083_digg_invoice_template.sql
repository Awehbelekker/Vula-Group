-- ============================================================
-- Vula Group — Migration 083: DIGG certified-payment invoice template
-- + a per-tenant "products & services" catalog for quick line-item add-on invoices.
-- Run in Supabase SQL editor.
-- ============================================================

-- ── Allow the new "digg" invoice look (JBCC certified-payment layout) ──────────
alter table commerce_invoice_settings drop constraint if exists commerce_invoice_settings_template_choice_check;
alter table commerce_invoice_settings
    add constraint commerce_invoice_settings_template_choice_check
    check (template_choice in ('classic','minimal','modern','branded','digg'));

-- ── Products & services catalog for quick-add on invoices/quotes ───────────────
-- Separate from commerce_products (that's OTH's storefront/stock catalog). This one is
-- per-tenant, has no stock/slug concept, and its description can carry a {project} token
-- that the invoice UI substitutes with whichever project is selected on the invoice.
create table if not exists commerce_invoice_items (
    id                uuid primary key default gen_random_uuid(),
    tenant_id         text not null,
    kind              text not null default 'service' check (kind in ('product', 'service')),
    name              text not null,               -- quick-pick label, e.g. "Site supervision"
    description       text,                        -- invoice line text; may contain {project}
    unit              text,                        -- ea/hr/day/m/m²/kg/% — free text
    unit_price_cents  int  not null default 0,
    active            bool not null default true,
    sort              int  not null default 0,
    doc_id            text,                        -- KB ingestion id, so it's retrievable/citable
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),
    unique(tenant_id, name)
);
create index if not exists idx_commerce_invoice_items_tenant on commerce_invoice_items (tenant_id, active, sort);

alter table commerce_invoice_items enable row level security;
drop policy if exists "tenant_isolation" on commerce_invoice_items;
create policy "tenant_isolation" on commerce_invoice_items
    using (tenant_id = current_setting('app.tenant_id', true));

drop trigger if exists trg_commerce_invoice_items_updated_at on commerce_invoice_items;
create trigger trg_commerce_invoice_items_updated_at
    before update on commerce_invoice_items
    for each row execute function set_updated_at();
