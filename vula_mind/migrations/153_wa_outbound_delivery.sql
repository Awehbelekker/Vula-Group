-- 153_wa_outbound_delivery.sql — know whether an outbound WhatsApp message actually ARRIVED.
--
-- 2026-09-06, reported by Ian ("it does feel that tenants are not receiving the message from
-- WhatsApp"). Confirmed in code: a 200 from Meta's send endpoint means ACCEPTED, not delivered.
-- _send_reply threw the response away entirely, so the message id (wamid) was never kept, and
-- Meta's asynchronous delivery callback — sent -> delivered -> read, or failed — arrived at a
-- handler that looked the wamid up in commerce_broadcast_recipients and, finding nothing,
-- silently returned. Every ordinary reply's delivery outcome was therefore discarded: a
-- customer's answer, an escalation nudge, a proof-of-payment question could all fail after
-- acceptance with nobody ever told.
--
-- The synchronous failure path (_record_send_failure) only ever caught errors Meta returns
-- inline; telemetry showed 0 WhatsApp send failures in 30 days while all three tenants sat
-- OUTSIDE the 24-hour re-engagement window, where free-form sends are exactly what fails.
--
-- Deliberately its own small table rather than a column on commerce_conversation_messages:
-- documents, buttons and nudges are sent from paths that never write a conversation row.

create table if not exists vula_wa_outbound (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    wamid        text not null,
    to_phone     text not null,
    kind         text not null default 'text',   -- text | document | buttons
    body_preview text,                           -- first ~200 chars, for "which message failed?"
    status       text not null default 'accepted', -- accepted|sent|delivered|read|failed
    error        text,
    notified_at  timestamptz,                    -- team told about a failure (nulls = not yet)
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create unique index if not exists vula_wa_outbound_wamid_idx on vula_wa_outbound (wamid);
create index if not exists vula_wa_outbound_tenant_idx on vula_wa_outbound (tenant_id, created_at desc);
-- Partial index: the only query that runs on a schedule is "failures nobody has been told about".
create index if not exists vula_wa_outbound_failed_idx on vula_wa_outbound (tenant_id)
    where status = 'failed' and notified_at is null;

alter table vula_wa_outbound enable row level security;

drop policy if exists vula_wa_outbound_service on vula_wa_outbound;
create policy vula_wa_outbound_service on vula_wa_outbound
    for all to service_role using (true) with check (true);
