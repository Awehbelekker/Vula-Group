-- 208_woocommerce_accounts.sql — a business's own WooCommerce store, connected to Vula.
--
-- 2026-10-13 (Ian: "WooCommerce will come up too"): the website import reads a WooCommerce
-- shop's public product feed once — published products only, no exact stock, no updates. A
-- connected store (REST API keys from WooCommerce › Settings › Advanced › REST API) gives the
-- whole catalogue with variations and stock, and with Read/Write keys WooCommerce calls Vula
-- when a product, stock level or order changes (webhooks signed with a secret Vula makes).
-- One row per tenant. Keys and the webhook secret are Fernet-encrypted by the API
-- (vula/integrations/woocommerce.py) before they reach this table. Idempotent. RLS on.

create table if not exists vula_woocommerce_accounts (
    tenant_id         text primary key,
    store_url         text not null,                 -- https://shop.example.co.za
    consumer_key      text,                          -- encrypted
    consumer_secret   text,                          -- encrypted
    webhook_secret    text,                          -- encrypted; signs WooCommerce's calls to Vula
    store_name        text,
    permissions       text,                          -- read | read_write
    status            text not null default 'connected',   -- connected | error | disconnected
    webhooks          jsonb not null default '[]'::jsonb,
    last_sync_at      timestamptz,
    last_sync         jsonb,
    last_error        text,
    connected_by      text,
    connected_at      timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);
create unique index if not exists uq_vula_woocommerce_accounts_store on vula_woocommerce_accounts (store_url)
    where status <> 'disconnected';
alter table vula_woocommerce_accounts enable row level security;

-- Which WooCommerce product / variation a Vula row came from, so a webhook updates the right one.
alter table commerce_products add column if not exists woo_id text;
create unique index if not exists uq_commerce_products_woo on commerce_products (tenant_id, woo_id)
    where woo_id is not null;
alter table commerce_product_variants add column if not exists woo_id text;
create unique index if not exists uq_commerce_variants_woo on commerce_product_variants (tenant_id, woo_id)
    where woo_id is not null;
