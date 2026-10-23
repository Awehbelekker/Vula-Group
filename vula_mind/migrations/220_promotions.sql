-- 220_promotions.sql — promotions the business forwards into its own WhatsApp line (23 Oct 2026).
--
-- Ian forwarded ~20 of Off the Hook's marketing posts into OTH's Vula line. Vula treated each
-- one as a customer message: tap menus, "we'll be in touch", and one put 2 × Hake Fillets in a
-- cart. A forward from the team is now kept here instead, used (a) as style examples when the
-- owner asks Vula to write a promotion, and (b) to answer "what's on special" for 7 days —
-- dated, and with prices only from the catalogue.
--
-- The server (service role) is the only reader/writer; RLS on with no policies. Idempotent.

create table if not exists vula_promotions (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    body        text not null,
    media_id    text,
    mime        text,
    source      text not null default 'forwarded',
    added_by    text,
    created_at  timestamptz not null default now()
);
create index if not exists idx_promotions_tenant on vula_promotions (tenant_id, created_at desc);
alter table vula_promotions enable row level security;
