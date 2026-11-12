-- 259: listings sent to people on WhatsApp, and what buyers/tenants are looking for
-- (2026-11-12, real-estate add-on, step 2). Only used by businesses with the `listings` module.
--
-- vula_listing_shares: one row per listing sent to a person as a WhatsApp card — by an agent
--   ("send Thandi the Durbanville house") or when a new listing matched their wish list and the
--   agent approved it. Lets Vula say "already sent on 3 Nov" instead of sending it twice.
--
-- vula_buyer_wishes: what a person told the business they want (sale or rent, budget, bedrooms,
--   areas, type). Saved from their own WhatsApp search or enquiry, never guessed. When a listing
--   goes on the market, the agent is shown which wishes it fits and decides who gets it — nothing
--   is sent to a person automatically. One row per person per business; `active=false` once they
--   ask to stop or the agent closes it (POPIA: they can be forgotten with the enquiry).
--
-- vula_property_enquiries.external_ref: where an enquiry came from outside Vula — a property
--   portal's enquiry email's Message-ID (vula/listings/portal_leads.py) — so it's filed once.
--
-- Money is integer cents. Idempotent. RLS on, service role only, like every other vula_* table.

create table if not exists vula_listing_shares (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    text not null,
  phone        text not null,                       -- normalised 27XXXXXXXXX
  property_id  uuid not null references vula_properties(id) on delete cascade,
  shared_by    text,                                -- agent / team member who sent it
  source       text not null default 'agent' check (source in ('agent', 'match')),
  created_at   timestamptz not null default now()
);
create index if not exists vula_listing_shares_lookup_idx
  on vula_listing_shares (tenant_id, phone, property_id);
alter table vula_listing_shares enable row level security;

create table if not exists vula_buyer_wishes (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       text not null,
  phone           text not null,                    -- normalised 27XXXXXXXXX
  name            text,
  listing_type    text check (listing_type in ('sale', 'rent')),
  max_price_cents bigint,
  min_bedrooms    numeric(4,1),
  suburbs         jsonb not null default '[]'::jsonb,   -- ["Durbanville", "Bellville"]
  property_types  jsonb not null default '[]'::jsonb,   -- ["house", "townhouse"]
  source          text not null default 'whatsapp',     -- whatsapp | website | agent
  active          boolean not null default true,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (tenant_id, phone)
);
create index if not exists vula_buyer_wishes_tenant_idx on vula_buyer_wishes (tenant_id, active);
alter table vula_buyer_wishes enable row level security;

alter table vula_property_enquiries add column if not exists external_ref text;
create index if not exists vula_property_enquiries_ref_idx
  on vula_property_enquiries (tenant_id, external_ref) where external_ref is not null;
