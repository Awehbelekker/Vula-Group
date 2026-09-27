-- 179_wa_inbound_tracking.sql — durable inbound WhatsApp work.
--
-- Text and voice messages are handled in a background task after the webhook returns 200 to
-- Meta (Meta's own timeout rule), so a redeploy or crash mid-run lost the message for good —
-- Meta won't redeliver it. The existing dedup row now carries the work's state: processing →
-- done (or failed / abandoned). A scheduler loop re-drives rows stuck in `processing`
-- (vula/api/whatsapp.py::redrive_stuck_inbound). The payload (message text / media id) is
-- cleared as soon as the message is handled; it's only kept while work is outstanding.
-- Idempotent.

alter table vula_wa_msg_dedup add column if not exists tenant_id  text;
alter table vula_wa_msg_dedup add column if not exists phone      text;
alter table vula_wa_msg_dedup add column if not exists kind       text;
alter table vula_wa_msg_dedup add column if not exists payload    jsonb;
alter table vula_wa_msg_dedup add column if not exists status     text;
alter table vula_wa_msg_dedup add column if not exists attempts   integer not null default 0;
alter table vula_wa_msg_dedup add column if not exists updated_at timestamptz;

create index if not exists idx_wa_dedup_processing on vula_wa_msg_dedup (updated_at)
    where status = 'processing';

alter table vula_wa_msg_dedup enable row level security;
