-- 075_marketing_saved_copy.sql — a per-tenant library of marketing copy the owner chose to
-- keep from the ✨ Marketing generator (P2.1), so good copy can be reused/edited later instead
-- of regenerated, and handed off to 📢 Broadcast without retyping.

create table if not exists commerce_saved_copy (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null,
    kind text not null,           -- specials | product | promo | broadcast
    topic text,
    tone text,
    body text not null,
    created_at timestamptz not null default now()
);

create index if not exists idx_saved_copy_tenant on commerce_saved_copy(tenant_id, created_at desc);
