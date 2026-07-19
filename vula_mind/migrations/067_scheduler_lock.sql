-- 067_scheduler_lock.sql — single shared lock so only ONE uvicorn worker runs the periodic
-- background jobs (delivery briefings, order-chase reminders, etc). start.py runs the API with
-- WEB_CONCURRENCY=2 workers for HTTP/webhook throughput, but every worker independently starting
-- the same wall-clock-timed loops meant every scheduled WhatsApp send fired twice (confirmed
-- 2026-07-16: duplicate delivery briefings, sales summaries, unpaid-order chases). This table is
-- a leased lock: the leader renews it periodically; if it dies, another worker takes over once
-- the lease expires, with no manual restart needed.

create table if not exists vula_scheduler_lock (
    lock_name   text primary key,
    holder      text not null,
    expires_at  timestamptz not null
);
