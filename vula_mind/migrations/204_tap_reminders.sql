-- ============================================================
-- Vula Group — Migration 204: tap-to-pay unpaid-bill reminders
-- A customer who saw their total and then left (or whose payment failed) leaves the bill 'abandoned'.
-- The sweeper then sends up to 3 reminders (10 min, next morning, day 3), 08:00-20:00 SAST only, at
-- most one per day, then the bill becomes 'needs_follow_up' for the owner. Every reminder is logged in
-- kb_reminders. A manual "resend" by the owner is logged too (kind='manual', seq 0).
--   kb_settings.reminders_max     0..3 reminders per bill; 0 = off (straight to needs_follow_up)
--   kb_bills.abandoned_at         when the customer left (drives the schedule)
--   kb_bills.last_session_id      the session whose payer/amount/tip the reminders reuse
--   kb_bills.closed_reason        why it was closed by hand: cash | eft | other | written_off
-- Idempotent. Run in Supabase SQL editor.
-- ============================================================

alter table kb_settings add column if not exists reminders_max int not null default 3
    check (reminders_max between 0 and 3);

alter table kb_bills add column if not exists abandoned_at timestamptz;
alter table kb_bills add column if not exists last_session_id uuid;
alter table kb_bills add column if not exists closed_reason text;
create index if not exists idx_kb_bills_unpaid on kb_bills (tenant_id, status) where status in ('abandoned', 'needs_follow_up');

alter table kb_reminders add column if not exists kind text not null default 'auto';   -- auto | manual
alter table kb_reminders add column if not exists channel text;                        -- text | template | none
-- the (bill_id, seq) uniqueness now applies to automatic reminders only, so owners can resend by hand
alter table kb_reminders drop constraint if exists kb_reminders_bill_id_seq_key;
alter table kb_reminders drop constraint if exists kb_reminders_seq_check;
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'kb_reminders_seq_range') then
        alter table kb_reminders add constraint kb_reminders_seq_range check (seq between 0 and 3);
    end if;
end $$;
create unique index if not exists uq_kb_reminders_auto on kb_reminders (bill_id, seq) where kind = 'auto';
