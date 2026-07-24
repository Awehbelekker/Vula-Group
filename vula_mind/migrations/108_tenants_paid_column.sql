-- 108_tenants_paid_column.sql — re-adds vula_tenants.paid, which migration 001 always declared
-- but was never actually applied to production (hand-run SQL, no schema_migrations tracking —
-- same class of gap as the WhatsApp chat-memory migration miss). Confirmed missing via
-- information_schema on 2026-07-24: PayFast's ITN webhook (onboarding.py) has been silently
-- failing to persist `paid: true` on real payments, and GET /v1/admin/signups' explicit
-- column select has been 400ing every call, both this whole time.

alter table vula_tenants add column if not exists paid boolean not null default false;
