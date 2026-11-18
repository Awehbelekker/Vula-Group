-- 291_books_review.sql — the business agrees its books audit before Vula signs it off.
--
-- 18 Nov 2026 (Ian, DIGG): the books review screen lets an owner or manager work through what the
-- books audit and the rebuild dry run found and fix each item (vula/commerce/books_review.py). When
-- it's clean, the business agrees (who, when, and what it accepts as still open); only then can the
-- Vula operator sign it off, and only a signed-off audit lets the books be rebuilt
-- (books_rebuild.commit). Two sides, on the record. Idempotent.

alter table vula_books_audits add column if not exists agreed_by   text;
alter table vula_books_audits add column if not exists agreed_at   timestamptz;
alter table vula_books_audits add column if not exists agreed_note text;
