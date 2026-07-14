-- ============================================================
-- Vula Group — Migration 052: remember each customer's language
-- Stores the language a customer usually messages in (detected from their voice notes via
-- Whisper, or from their text) so Vula greets and replies in isiZulu/Afrikaans/etc. from the
-- first message — and uses it for confirmations, reminders, and broadcasts. Still adapts if
-- they switch mid-chat. Run in Supabase SQL editor.
-- ============================================================

alter table commerce_conversation_sessions
    add column if not exists preferred_language text;   -- ISO-ish code: en | af | zu | xh | st | ...
