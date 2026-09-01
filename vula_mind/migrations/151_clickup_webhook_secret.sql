-- 151_clickup_webhook_secret.sql — authenticate the inbound ClickUp webhook.
--
-- Found 2026-09-01: POST /v1/clickup/webhook accepted ANY request. It carried no signature
-- check of any kind, yet it mutates real state — it updates field-ops task status and can
-- trigger procurement posting. Anyone who knew the URL could mark work complete or move stock.
-- Same class of hole as the unauthenticated Yoco webhook fixed on 2026-08-09; the HMAC pattern
-- already exists in vula/payments/__init__.py.
--
-- ClickUp signs every delivery with a secret unique to the webhook, returned when the webhook is
-- created (X-Signature = HMAC-SHA256 of the raw body, hex; verified against ClickUp's own docs at
-- developer.clickup.com/docs/webhooksignature). That secret was being thrown away — register
-- only checked that an "id" came back — so there was nothing to verify against.
--
-- Stored Fernet-encrypted, same as api_token on this table.

ALTER TABLE vula_clickup_accounts
    ADD COLUMN IF NOT EXISTS webhook_secret text,
    ADD COLUMN IF NOT EXISTS webhook_id     text;
