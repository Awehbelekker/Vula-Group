# Old Supabase CLI migrations — not used

These five files (May–Aug 2026) were applied to production before Vula's numbered migrations
took over (`vula_mind/migrations/`, applied with `tools/migrate.py`). Everything they created is
now in the numbered files — the last missing pieces (merchandising columns, `commerce_reviews`,
`followup_sent_at`) were folded into `migrations/242_production_parity.sql` on 3 Nov 2026.

Kept for history only. Don't add files here and don't run them: the local Supabase for staging
lives in `infrastructure/local-supabase/` and gets its schema from the numbered migrations.
