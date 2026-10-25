-- 223_whatsapp_coexistence.sql — a business's WhatsApp Business app number shared with Vula
-- (25 Oct 2026, Ian: "if we want tenant to use the current WhatsApp number … so tenant barely
-- need to use the Meta dashboard").
--
-- Meta's "coexistence": during Embedded Signup the owner links the WhatsApp Business app number
-- they already use; they keep the app on their phone and Vula answers on the same number through
-- the Cloud API. Such a number must never be re-registered (that would take it off the app), and
-- its owner's own replies arrive as smb_message_echoes webhooks (vula/api/whatsapp_coexist.py).
--
-- `coexistence` marks those rows; `history_sync` records what Meta said when Vula asked for the
-- app's contacts and chat history right after connecting. Adds columns to an existing table
-- (RLS already on, migration 003). Idempotent.

alter table vula_whatsapp_accounts
    add column if not exists coexistence boolean not null default false,
    add column if not exists history_sync jsonb;
