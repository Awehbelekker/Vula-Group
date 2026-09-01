-- 148_voice_retry_queue.sql — never lose a voice order to a transcription outage.
--
-- Real telemetry (2026-09-01): 6 of 23 voice notes ever received — 26% — were lost to a bare
-- 530 from the local Whisper tunnel (the SA GPU unreachable, never a transcription failure).
-- The customer was told "please type it out" and, in practice, several simply didn't.
--
-- Rather than send customer audio to a third-party cloud transcriber, a failed note is parked
-- here with its audio and retried locally once the box is back. The audio is stored base64 in
-- a text column deliberately: real notes measured 15KB–175KB (≈240KB base64), well within
-- Postgres's comfort zone, and it avoids standing up a storage bucket for what is a transient
-- queue. Rows are deleted once done, so this table stays near-empty in steady state.

CREATE TABLE IF NOT EXISTS vula_voice_retry_queue (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       text NOT NULL,
    customer_phone  text NOT NULL,
    msg_id          text,
    mime_type       text NOT NULL DEFAULT 'audio/ogg',
    audio_b64       text NOT NULL,
    route_mode      text NOT NULL DEFAULT 'commerce',
    status          text NOT NULL DEFAULT 'pending',   -- pending | done | gave_up
    attempts        int  NOT NULL DEFAULT 0,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_attempt_at timestamptz
);

-- The retry loop's only query: oldest pending first.
CREATE INDEX IF NOT EXISTS idx_voice_retry_pending
    ON vula_voice_retry_queue (status, created_at)
    WHERE status = 'pending';

-- One queued entry per inbound WhatsApp message, so a webhook redelivery (which Meta does
-- after a container restart) can't queue the same voice note twice and transcribe it twice.
CREATE UNIQUE INDEX IF NOT EXISTS idx_voice_retry_msg_id
    ON vula_voice_retry_queue (msg_id)
    WHERE msg_id IS NOT NULL;

ALTER TABLE vula_voice_retry_queue ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS voice_retry_service_role ON vula_voice_retry_queue;
CREATE POLICY voice_retry_service_role ON vula_voice_retry_queue
    FOR ALL TO service_role USING (true) WITH CHECK (true);
