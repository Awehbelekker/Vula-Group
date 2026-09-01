-- 150_learned_answers_review.sql — an unreviewed learned answer must never reach a customer.
--
-- Audited on 2026-09-01, ahead of off-the-hook going live. vula_learned_answers held exactly
-- two rows, both off-the-hook, and BOTH were wrong:
--
--   Q: "What is in the family fish box?"
--   A: "Yes I can do"                       <- the helper replying about something else
--
--   Q: "Do you deliver to Timbuktu"
--   A: "Respond to Richard Downing via WhatsApp business and say delivery will be on Monday
--       between 10:00 - 12:00"              <- the helper instructing Vula, naming a real customer
--
-- Both were live and being served: probing production, "do you deliver to Milnerton" returned
-- the Timbuktu answer (Jaccard 0.6, over the old 0.5 bar — the only differing token was the
-- place name). So the answer a customer got depended on a word the matcher ignored.
--
-- Learning now goes through a one-tap owner review: the first time an answer is captured it is
-- 'pending' and unusable; the owner Keeps or Bins it on WhatsApp; only 'approved' rows are ever
-- retrieved thereafter. Existing rows are deliberately left 'pending' so nothing already stored
-- keeps being served on the strength of never having been checked.

ALTER TABLE vula_learned_answers
    ADD COLUMN IF NOT EXISTS status      text NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS approved_at timestamptz,
    ADD COLUMN IF NOT EXISTS approved_by text;

-- The only retrieval path: this tenant's approved answers, newest first.
CREATE INDEX IF NOT EXISTS idx_learned_answers_approved
    ON vula_learned_answers (tenant_id, created_at DESC)
    WHERE status = 'approved';
