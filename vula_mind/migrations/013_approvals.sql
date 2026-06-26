-- 013_approvals.sql
-- Generic multi-approver engine. One approval can require several approvers
-- (architect + QS + client…); all must approve before the action fires.
-- Works for any entity (invoice, task, quote…). On invoice approval the engine
-- delivers to the client via WhatsApp / email / both.

create table if not exists vula_approvals (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    entity_type   text not null,                 -- invoice | task | quote
    entity_id     text not null,
    title         text default '',               -- human label for the WhatsApp prompt
    requested_by  text default '',               -- phone/email of requester
    status        text not null default 'pending', -- pending | approved | rejected
    deliver_via   text default '',               -- invoice: whatsapp | email | both
    meta          jsonb default '{}'::jsonb,
    created_at    timestamptz not null default now(),
    completed_at  timestamptz
);
create index if not exists idx_approvals_tenant on vula_approvals (tenant_id, status);
create index if not exists idx_approvals_entity on vula_approvals (entity_type, entity_id);

create table if not exists vula_approval_steps (
    id            uuid primary key default gen_random_uuid(),
    approval_id   uuid not null references vula_approvals(id) on delete cascade,
    approver_phone text not null,                -- E.164 digits
    approver_name  text default '',
    role           text default 'approver',      -- architect | quantity_surveyor | client | owner
    status         text not null default 'pending', -- pending | approved | rejected
    notes          text default '',
    decided_at     timestamptz
);
create index if not exists idx_approval_steps_approval on vula_approval_steps (approval_id);
create index if not exists idx_approval_steps_phone on vula_approval_steps (approver_phone, status);
