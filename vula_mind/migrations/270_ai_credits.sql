-- 270_ai_credits.sql — a business's AI allowance and the top-ups it buys.
--
-- 14 Nov 2026 (Ian: Vula runs the AI and bills each business — "allowance + top-ups"). Every
-- plan includes a month of cloud AI (Starter R100, Growth R300, Business R750, at Vula's price:
-- cost × usd_zar_rate × ai_markup). Use on Vula's own model is free and never counts. When the
-- allowance and any top-ups are used up, only the extras (Ask anything, research) pause; customer
-- replies, document reading and the business assistant never stop (vula/ai_allowance.py).
--
--   vula_ai_credits      a top-up the owner requested (paid by EFT and marked paid in Master)
--                        or credit Master granted. amount_cents is the AI it adds, in rand at
--                        Vula's price. A paid top-up lasts until it's used up.
--   vula_tenant_config.ai_allowance_cents   a per-business allowance; null = the plan's.
--
-- Read and written only by the API with the service key. RLS on, no policies. Idempotent.

create table if not exists vula_ai_credits (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    kind            text not null default 'topup' check (kind in ('topup', 'grant')),
    amount_cents    bigint not null check (amount_cents > 0),
    status          text not null default 'requested' check (status in ('requested', 'paid', 'cancelled')),
    requested_by    text,
    requested_at    timestamptz not null default now(),
    paid_at         timestamptz,
    paid_by         text,
    reference       text,
    note            text
);
create index if not exists idx_ai_credits_tenant on vula_ai_credits (tenant_id, status);
alter table vula_ai_credits enable row level security;

alter table vula_tenant_config add column if not exists ai_allowance_cents bigint;
