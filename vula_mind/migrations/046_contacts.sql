-- ============================================================
-- Vula Group — Migration 046: imported contact book (commerce_contacts)
-- A first-class home for an imported existing-client list (e.g. Off the Hook's area lists),
-- deduped by phone. Unioned into _aggregate_customers so these contacts show in the Customers
-- tab and are reachable by the broadcast pipeline — but only once consent flips to 'opted_in'.
-- Run in Supabase SQL editor.
-- ============================================================

create table if not exists commerce_contacts (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    phone          text not null,                 -- normalized 27XXXXXXXXX
    name           text,
    email          text,
    area           text,                          -- source list: tableview | sunningdale | ...
    product        text,                          -- fish | chicken | tuna
    tags           text[] not null default '{}',  -- lcc | wcbac | remax | recovery | collect | ...
    address        text,
    consent_status text not null default 'unknown',   -- unknown | opted_in | opted_out
    source         text not null default 'import',
    notes          text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    unique (tenant_id, phone)                      -- idempotent upsert key
);

create index if not exists idx_commerce_contacts_tenant on commerce_contacts (tenant_id);
create index if not exists idx_commerce_contacts_consent on commerce_contacts (tenant_id, consent_status);

alter table commerce_contacts enable row level security;
drop policy if exists "tenant_isolation" on commerce_contacts;
create policy "tenant_isolation" on commerce_contacts
    using (tenant_id = current_setting('app.tenant_id', true));
