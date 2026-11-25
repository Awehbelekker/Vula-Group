-- 310_product_guides.sql — spec sheets on products and product guides a business sends to its
-- clients (vula/commerce/product_guides.py, 25 Nov 2026).
--
-- Ian, 25 Nov: Floornet's "Field Guide for Specifiers" (one HTML page, products typed into it by
-- hand) is a really nice tool to have — for a Gerflor distributor, a flooring contractor, any
-- business that wants to send a client its range with the specs and a "check stock" button.
--
--   commerce_products.specs — the product's spec sheet, cleaned in code (product_guides.clean_specs):
--       {"grade": "Grade 33 · Commercial", "benefits": ["Stain resistant", …],
--        "rows": [["Backing", "Felt back"], ["Width", "4 m"], …], "link": "https://…"}
--       Null = no spec sheet. Never a price: prices stay price_cents.
--   commerce_product_guides — one guide = a secret link the business sends: its title, the
--       client it is for, an intro line, which products (categories and/or chosen products; both
--       empty = every listed product), whether prices are shown. `token` is the only way in; the
--       page is never indexed. Turned off = the link stops working.
--
-- Idempotent.

alter table commerce_products add column if not exists specs jsonb;

create table if not exists commerce_product_guides (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    token           text not null unique,
    title           text not null,
    client_name     text,
    intro           text,
    categories      jsonb not null default '[]'::jsonb,
    product_ids     jsonb not null default '[]'::jsonb,
    show_prices     boolean not null default false,
    active          boolean not null default true,
    views           integer not null default 0,
    last_viewed_at  timestamptz,
    created_by      text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists idx_product_guides_tenant on commerce_product_guides (tenant_id, created_at desc);

alter table commerce_product_guides enable row level security;
drop policy if exists "tenant_isolation" on commerce_product_guides;
create policy "tenant_isolation" on commerce_product_guides
    using (tenant_id = current_setting('app.tenant_id', true));

comment on table commerce_product_guides is 'A product guide a business sends its clients: a secret link to its range with spec sheets, optionally prices, and a WhatsApp stock check.';
