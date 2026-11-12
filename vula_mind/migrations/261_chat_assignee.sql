-- 261_chat_assignee.sql — a customer chat assigned to a real team member (12 Nov 2026).
--
-- assigned_to was free text ("Thandi"), so nobody could filter by person or see "my chats".
-- assigned_member_id is the vula_team_members.id; assigned_to keeps the name for display.
-- Someone without Inbox access sees only the chats assigned to them (vula/api/commerce.py
-- _inbox_access); owners, managers and Inbox users see every chat, filterable by person.
-- Idempotent.

alter table commerce_conversation_sessions add column if not exists assigned_member_id text;
create index if not exists idx_conv_sessions_assignee
    on commerce_conversation_sessions (tenant_id, assigned_member_id, updated_at desc);
