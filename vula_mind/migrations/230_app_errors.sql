-- 230_app_errors.sql — crashes in the dashboard on people's phones and computers (2026-10-27).
--
-- Until now a screen that broke on a customer's phone was invisible unless they told Ian. The
-- dashboard reports its own uncaught errors (src/lib/appErrors.js → POST /v1/app-errors,
-- vula/app_errors.py); Master › Health lists them. One row per distinct error per build per
-- business, counted — never one row per occurrence.
--
-- POPIA: no customer content. The message is scrubbed of emails, numbers of 4+ digits, ids and
-- URL queries before it's stored; frames are file:line only; the browser is "Chrome 141 ·
-- Android". tenant_id only when the signed-in session proves membership; no user, no IP.
-- RLS on with no policies: the service key only. Idempotent.

create table if not exists vula_app_errors (
    id           uuid primary key default gen_random_uuid(),
    fingerprint  text not null unique,
    tenant_id    text,
    build        text,
    page         text,
    message      text not null,
    frames       text,
    browser      text,
    count        int not null default 1,
    first_seen   timestamptz not null default now(),
    last_seen    timestamptz not null default now(),
    resolved_at  timestamptz
);
create index if not exists idx_app_errors_last_seen on vula_app_errors (last_seen desc);

alter table vula_app_errors enable row level security;
