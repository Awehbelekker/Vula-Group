-- 108_team_member_window_tracking.sql — track each team member's WhatsApp 24h conversation
-- window so a "keep it open" nudge can fire before it closes, instead of every proactive
-- follow-up hitting the template wall. Generalized (any team member, any tenant) — see
-- vula/api/whatsapp.py's inbound dispatch (stamps last_message_at) and _send_wa_template
-- (stamps last_notified_at), plus the new nudge check in the automations-style poller.

alter table vula_team_members
    add column if not exists last_message_at  timestamptz,  -- last inbound WhatsApp FROM this member
    add column if not exists last_notified_at timestamptz;  -- last proactive template send TO this member
