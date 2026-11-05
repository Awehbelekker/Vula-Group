-- 246_buying.sql — Vula buys for a business: find the best price, the owner approves, then pay.
--
-- 5 Nov 2026: "find ink cartridges for the Canon printer, best price, I confirm". Vula searches
-- only the shops the business allowed, shows prices as they appear on each shop's page, and
-- nothing is bought until an approver says yes. It never holds money, a card or a shop password:
-- an online shop gets a checkout link the approver pays (card, Google Pay, Apple Pay); a supplier
-- the business has an account with gets a purchase order (migration 077).
--
--   vula_buying_settings  one row per business: on/off (off by default), limits, who may ask,
--                         who approves, where purchases are booked by default
--   vula_buying_shops     the shops Vula may buy from: a website (checkout link) or a supplier
--                         on account (commerce_suppliers.id)
--   vula_purchases        each search (draft, with the offers found) and each purchase through
--                         approval, payment and receipt
--
-- Money in integer cents. Read and written by the backend with the service key only.
-- RLS on, no policies. Idempotent.
create table if not exists vula_buying_settings (
    tenant_id                text primary key,
    enabled                  boolean not null default false,
    per_purchase_limit_cents bigint not null default 0,      -- 0 = no purchase allowed until set
    monthly_limit_cents      bigint not null default 0,      -- 0 = no monthly cap beyond the per-purchase one
    who_can_ask              text not null default 'managers' check (who_can_ask in ('owner', 'managers', 'team')),
    approver_phones          text[] not null default '{}',   -- empty = the owners/admins
    default_project          text,
    default_account          text,
    updated_by               text,
    updated_at               timestamptz not null default now()
);
alter table vula_buying_settings enable row level security;

create table if not exists vula_buying_shops (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    name        text not null,
    domain      text,                                         -- e.g. shop.example.co.za (checkout link)
    how         text not null default 'checkout_link' check (how in ('checkout_link', 'account')),
    supplier_id text,                                         -- commerce_suppliers.id (account)
    active      boolean not null default true,
    created_by  text,
    created_at  timestamptz not null default now()
);
create unique index if not exists idx_buying_shops_name
    on vula_buying_shops (tenant_id, lower(name)) where active;
alter table vula_buying_shops enable row level security;

create table if not exists vula_purchases (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    status          text not null default 'draft'
                    check (status in ('draft', 'pending_approval', 'approved', 'ordered', 'paid',
                                      'rejected', 'cancelled')),
    request         text,                                     -- what was asked for, as typed
    offers          jsonb not null default '[]',              -- the options shown (draft)
    shop_id         uuid,
    shop_name       text,
    how             text,
    items           jsonb not null default '[]',              -- [{name, quantity, unit_price_cents, url}]
    total_cents     bigint not null default 0,
    link            text,
    project         text,
    account_code    text,
    requested_by    text,                                     -- phone
    requested_name  text,
    approval_id     uuid,                                     -- vula_approvals.id
    approved_by     text,
    po_id           uuid,                                     -- commerce_purchase_orders.id
    expense_id      uuid,                                     -- the receipt it was matched to
    bank_txn_id     uuid,
    notes           text,
    created_at      timestamptz not null default now(),
    approved_at     timestamptz,
    paid_at         timestamptz,
    reminded_at     timestamptz
);
create index if not exists idx_purchases_tenant on vula_purchases (tenant_id, created_at desc);
create index if not exists idx_purchases_open on vula_purchases (status, approved_at)
    where status in ('approved', 'ordered');
alter table vula_purchases enable row level security;
