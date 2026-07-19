-- 084_storefront_presentation_settings.sql — gives hero copy/announcements/cutoff-time a real
-- home on commerce_order_settings. Found while retiring off_the_hook's own /admin/settings page
-- (P2, "store admin reconciliation" plan): this content (hero_tagline, hero_subtitle,
-- announcements, cutoff_time, express_delivery_extra_cents, featured_product_ids) was real,
-- live data sitting ONLY in off_the_hook's local data/store-settings.json — the retirement would
-- have silently discarded it. These aren't strictly "order" settings, but commerce_order_settings
-- is already the closest thing to a general per-tenant storefront-config table, and adding a
-- handful of presentation columns here is far simpler than inventing a new table right now.

alter table commerce_order_settings
    add column if not exists hero_tagline text,
    add column if not exists hero_subtitle text,
    add column if not exists announcements jsonb,
    add column if not exists cutoff_time text,
    add column if not exists express_delivery_extra_cents integer,
    add column if not exists featured_product_ids jsonb;
