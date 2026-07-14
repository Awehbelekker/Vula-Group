-- Vula Group — Migration 061: Company cards + whose-money on expense claims
-- Run in: Supabase Dashboard > SQL Editor > New Query > Run
-- ============================================================================
-- "Whose money paid for this?" is what decides reimbursement — not who sent the
-- receipt. Register the business card(s) by last-4; Vula reads the card digits
-- off the receipt: company card → business money (reconciles against the bank
-- statement, nothing owed); personal card / cash → out of pocket (reimbursable).

create table if not exists commerce_cards (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    last4       text not null,                 -- last 4 digits printed on slips
    label       text,                          -- e.g. "OTH Capitec business card"
    active      boolean not null default true,
    created_at  timestamptz not null default now(),
    unique (tenant_id, last4)
);
create index if not exists idx_commerce_cards_tenant on commerce_cards (tenant_id);
alter table commerce_cards enable row level security;
drop policy if exists "tenant_isolation" on commerce_cards;
create policy "tenant_isolation" on commerce_cards
    using (tenant_id = current_setting('app.tenant_id', true));

-- Whose money on each claim + the card digits read off the receipt.
alter table commerce_expenses
    add column if not exists paid_with  text,    -- 'company_card' | 'personal' | 'cash' | null=unknown
    add column if not exists card_last4 text;

create index if not exists idx_commerce_expenses_paid_with
    on commerce_expenses (tenant_id, paid_with);
