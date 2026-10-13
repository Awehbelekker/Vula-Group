-- 207_shopify_accounts.sql — a business's own Shopify store, connected to Vula.
--
-- 2026-10-13 (SeedleMe, a Shopify shop): reading the public storefront gave products only,
-- slowly, and Shopify rate-limited it. A connected store gives the whole catalogue (drafts,
-- every image, live stock), and Shopify tells Vula when something changes (webhooks).
-- One row per tenant. Credentials are Fernet-encrypted by the API (vula/integrations/shopify.py)
-- before they reach this table. Idempotent. RLS on; the service role (the API) only.

create table if not exists vula_shopify_accounts (
    tenant_id         text primary key,
    shop_domain       text not null,                 -- xxx.myshopify.com
    auth_mode         text not null default 'client_credentials',   -- client_credentials | token
    client_id         text,
    client_secret     text,                          -- encrypted; also signs webhooks
    access_token      text,                          -- encrypted: a shpat_ token, or the cached 24h one
    token_expires_at  timestamptz,
    scopes            text[],
    shop_name         text,
    status            text not null default 'connected',            -- connected | error | disconnected
    webhooks          jsonb not null default '[]'::jsonb,
    last_sync_at      timestamptz,
    last_sync         jsonb,
    last_error        text,
    connected_by      text,
    connected_at      timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);
create unique index if not exists uq_vula_shopify_accounts_shop on vula_shopify_accounts (shop_domain)
    where status <> 'disconnected';
alter table vula_shopify_accounts enable row level security;

-- Which Shopify product / variant a Vula row came from, so a webhook updates the right one.
alter table commerce_products add column if not exists shopify_id text;
create unique index if not exists uq_commerce_products_shopify on commerce_products (tenant_id, shopify_id)
    where shopify_id is not null;
alter table commerce_product_variants add column if not exists shopify_id text;
create unique index if not exists uq_commerce_variants_shopify on commerce_product_variants (tenant_id, shopify_id)
    where shopify_id is not null;
