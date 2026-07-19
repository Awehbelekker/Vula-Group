-- 068_team_welcome.sql — tracks whether a recognized owner/staff member has been sent their
-- one-time welcome message on first WhatsApp contact (e.g. after being added to the team, or
-- after a phone number change). NULL = not yet welcomed.

alter table vula_team_members
    add column if not exists welcomed_at timestamptz;
