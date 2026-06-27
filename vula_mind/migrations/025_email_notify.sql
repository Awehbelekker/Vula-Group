-- 025_email_notify.sql — who to ask on WhatsApp when an auto-filed email doc can't
-- be matched to a project. Replies (via the tenant's assistant line) resolve it through
-- the existing pending-project flow.
alter table vula_email_accounts add column if not exists notify_phone text;
