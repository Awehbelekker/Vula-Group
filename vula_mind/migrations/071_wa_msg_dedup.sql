-- 071_wa_msg_dedup.sql — durable cross-worker dedup for inbound WhatsApp messages.
-- The webhook's idempotency list is in-memory PER WORKER (WEB_CONCURRENCY=2) and lost on
-- restart, so a Meta redelivery (slow LLM reply → webhook timeout → retry) landing on the other
-- worker gets processed again. Confirmed 3x on 2026-07-16 alone: double escalation-answer,
-- double voice-note reply, double Timbuktu reply (two DIFFERENT generations of the same
-- question). Inserting the msg_id here is the claim — the primary key makes it atomic.

create table if not exists vula_wa_msg_dedup (
    msg_id  text primary key,
    seen_at timestamptz not null default now()
);

-- Rows are only needed for as long as Meta might retry (minutes); an index for cheap cleanup.
create index if not exists idx_wa_dedup_seen on vula_wa_msg_dedup (seen_at);
