-- 086_oth_settings_data_migration.sql — one-time data migration (not schema).
-- Moves the real content that was only sitting in off_the_hook's own local
-- data/store-settings.json into the columns migration 084 just added to
-- commerce_order_settings, so nothing is lost when that page's own /admin/settings
-- is retired (Phase 2 of the store-admin-reconciliation plan).
--
-- delivery_fee_cents (8000) and free_delivery_over_cents (50000) are NOT touched here —
-- they already matched the live DB row before this migration was written.
--
-- Run this AFTER 084 (needs its columns to exist). Requires an existing
-- commerce_order_settings row for off-the-hook (confirmed present).

update commerce_order_settings
set hero_tagline = 'Fresh fish this week. Pasture-raised chicken. Frozen seafood.',
    hero_subtitle = 'Cape Town delivery — order in 60 seconds on WhatsApp.',
    cutoff_time = '10:00',
    express_delivery_extra_cents = 5000,
    featured_product_ids = '[]'::jsonb,
    announcements = '[
        "🐟  Fresh fish this week: Yellowfin Tuna R290/kg · Hake R160/kg · Kingklip R240/kg",
        "🐓  Pasture-raised chicken — GMO-free · No hormones · No brine",
        "🦐  Frozen seafood packs — Calamari, Prawns, Mussels & more",
        "📦  Order by 10am for same-day delivery — Cape Town",
        "🟢  WhatsApp ordering: +27 79 178 3933 — reply in minutes",
        "✅  All items sold by weight — what you order is what you pay for"
    ]'::jsonb
where tenant_id = 'off-the-hook';
