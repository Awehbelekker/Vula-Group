-- 106_email_campaigns.sql — bulk/campaign email sending, distinct from the existing 1:1
-- business correspondence (vula/email_imap — search/read/draft/send one email at a time).
-- Fifth gap closed from the marketing capability audit: "no bulk/campaign sending, no
-- template library, no subscriber list, no open/click tracking for email anywhere."
--
-- v1 scope: plain-text send to a segment, reusing the EXACT SAME commerce_segments /
-- _aggregate_customers targeting already built for WhatsApp broadcasts (a customer's email
-- is already merged into that same aggregate). Open/click tracking for email is deliberately
-- NOT included — a tracking pixel + per-recipient rewritten links is a materially bigger
-- build than fits alongside this, and can be added later without breaking anything here.
--
-- A real unsubscribe mechanism IS included (not deferred) — POPIA/CAN-SPAM require it, and
-- shipping bulk email without one would be a real compliance gap, unlike deferred tracking
-- which is a nice-to-have.

create table if not exists commerce_email_campaigns (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        text not null,
    name             text,
    subject          text not null,
    body             text not null,
    audience_filter  text not null default 'all',
    recipient_count  integer not null default 0,
    sent_count       integer not null default 0,
    failed_count     integer not null default 0,
    status           text not null default 'draft',   -- draft | sending | sent | failed
    created_at       timestamptz not null default now(),
    sent_at          timestamptz
);
create index if not exists idx_email_campaigns_tenant on commerce_email_campaigns (tenant_id);

create table if not exists commerce_email_campaign_recipients (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    campaign_id  uuid not null,
    email        text not null,
    status       text not null default 'sent',   -- sent | failed
    error        text,
    created_at   timestamptz not null default now()
);
create index if not exists idx_email_campaign_recipients_campaign on commerce_email_campaign_recipients (campaign_id);

-- Mirrors commerce_consent (migration 020) but keyed by email instead of phone.
create table if not exists commerce_email_consent (
    id         uuid primary key default gen_random_uuid(),
    tenant_id  text not null,
    email      text not null,
    status     text not null default 'opted_in',   -- opted_in | opted_out
    source     text,                                -- unsubscribe_link | dashboard
    updated_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    unique (tenant_id, email)
);
create index if not exists idx_email_consent_tenant on commerce_email_consent (tenant_id, status);

alter table commerce_email_campaigns enable row level security;
drop policy if exists "tenant_isolation" on commerce_email_campaigns;
create policy "tenant_isolation" on commerce_email_campaigns
    using (tenant_id = current_setting('app.tenant_id', true));

alter table commerce_email_campaign_recipients enable row level security;
drop policy if exists "tenant_isolation" on commerce_email_campaign_recipients;
create policy "tenant_isolation" on commerce_email_campaign_recipients
    using (tenant_id = current_setting('app.tenant_id', true));

alter table commerce_email_consent enable row level security;
drop policy if exists "tenant_isolation" on commerce_email_consent;
create policy "tenant_isolation" on commerce_email_consent
    using (tenant_id = current_setting('app.tenant_id', true));
