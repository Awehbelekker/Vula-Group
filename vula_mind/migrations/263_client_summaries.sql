-- 263_client_summaries.sql — what Vula remembers about a client between conversations.
--
-- 12 Nov 2026 (Ian): "if a client has been chatting and after a month Vula does not remember the
-- conversation … it could be very frustrating". The model reads only the last 24 hours of a
-- conversation, so a returning client was a stranger apart from their last delivery address. The
-- messages were all still stored; the model never saw them.
--
-- One short, dated note per client per business, written when a conversation goes quiet and
-- folded into the next one (vula/client_memory.py). It records what was said. Amounts, orders and
-- invoices are always read from their own tables, never from this note.
--
--   client_key      the client's WhatsApp number in 27… form
--   summary         the note: short dated lines, newest last
--   covers_until    the created_at of the newest message the note has taken in
--
-- Deleted by "delete my data" (vula/privacy_data.py) and with the conversation it summarises
-- (vula/retention.py). Read and written only by the API with the service key. RLS on, no
-- policies. Idempotent.

create table if not exists commerce_client_summaries (
    tenant_id       text not null,
    client_key      text not null,
    summary         text not null default '',
    covers_until    timestamptz,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    primary key (tenant_id, client_key)
);
alter table commerce_client_summaries enable row level security;
