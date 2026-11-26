-- 313_lesson_paid_by_tap.sql — a lesson paid in person by Tap to Pay (vula/commerce/lessons.py,
-- vula/tap/appapi.py, 26 Nov 2026).
--
-- A coach charges a confirmed lesson from the staff app: a Tap to Pay bill is opened on the coach's
-- tag for what the lesson costs, and the customer taps and pays (and can add a tip). The request
-- keeps which bill that is; when the gateway confirms the payment the request is marked paid.
-- The money is booked by Tap to Pay's own ledger, so the request's invoice is NOT paid as well.
-- Idempotent.

alter table commerce_lesson_requests add column if not exists paid_at timestamptz;
alter table commerce_lesson_requests add column if not exists paid_via text;
alter table commerce_lesson_requests add column if not exists tap_bill_id uuid;
create index if not exists idx_lesson_requests_tap_bill on commerce_lesson_requests (tenant_id, tap_bill_id)
    where tap_bill_id is not null;
