-- 189: the owner's own answers about their business (2026-09-30, Ian: "if I'm onboarding a new
-- tenant … how do we ensure we don't have these errors or hallucinations?"). A short interview on
-- WhatsApp or in the dashboard replaces the model-drafted starter KB's [placeholders] with facts
-- the owner stated. Answers are also written into the tenant's knowledge base as one document.
-- Idempotent. RLS on (service role only, like every other vula_* table).

create table if not exists vula_business_profile (
  tenant_id   text primary key,
  answers     jsonb not null default '{}'::jsonb,
  updated_by  text,
  updated_at  timestamptz not null default now()
);

alter table vula_business_profile enable row level security;
