-- 157_media_dedup.sql — one inbound file is processed ONCE, atomically.
--
-- 2026-09-10, DIGG: a rep sent one "Payment Notification.pdf" and got the "📄 Got it —
-- processing…" ack EIGHT times, then five full "Filed / here's the analysis / which project?"
-- replies for it, then three more. Two gaps:
--   1. the ack is sent before any dedup runs, so N deliveries = N acks;
--   2. the existing redelivery guard is a READ of vula_filed_documents.content_hash — with
--      several near-simultaneous handlers, none has filed yet, so every one passes the check
--      and does the full (expensive) vision-scan + KB-ingest + reply.
--
-- This table makes the check an atomic CLAIM: the primary key rejects the 2nd..Nth insert
-- whichever worker/delivery it is, exactly like vula_wa_msg_dedup (migration 071) does for
-- message ids — but keyed on the FILE's content hash (Meta sends sha256 in the webhook), so
-- it also catches a genuine re-send of the same file a minute later under a new message id.
--
-- created_at lets a re-send of the SAME file days later re-process (the claim is refreshed if
-- the existing one is older than the reclaim window the code applies).

create table if not exists vula_media_dedup (
    tenant_id      text not null,
    content_sha    text not null,     -- Meta's document/image sha256, or a hash of the bytes
    kind           text,              -- 'document' | 'image' | 'video'
    claimed_by     text,              -- phone that sent it (diagnostics only)
    created_at     timestamptz not null default now(),
    primary key (tenant_id, content_sha)
);

create index if not exists vula_media_dedup_age_idx on vula_media_dedup (created_at);

alter table vula_media_dedup enable row level security;

drop policy if exists vula_media_dedup_service on vula_media_dedup;
create policy vula_media_dedup_service on vula_media_dedup
    for all to service_role using (true) with check (true);
