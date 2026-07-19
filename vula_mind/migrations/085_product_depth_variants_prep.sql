-- 085_product_depth_variants_prep.sql — closes the schema gap for fields that only ever existed
-- in off_the_hook's disconnected local product-overrides.json (P2, "store admin reconciliation"
-- plan), plus SEO + draft/unlisted status (P3 depth), plus a lightweight per-product options
-- list ready for Phase 4 (variants).

alter table commerce_products
    -- Weight-based pricing (the real July-6 feature — cart.ts already depends on these existing
    -- durably; today they only exist in a local JSON file on the storefront's own disk).
    add column if not exists pricing_mode text not null default 'fixed'
        check (pricing_mode in ('fixed', 'by_weight')),
    add column if not exists price_per_kg_cents integer,
    add column if not exists min_weight_g integer,
    add column if not exists max_weight_g integer,
    add column if not exists reference_weight_g integer,
    -- Storytelling fields specific to this real tenant's product editor — kept as first-class
    -- columns rather than a generic metadata blob (small, already proven useful).
    add column if not exists catch_source text,
    add column if not exists fisherman_name text,
    -- SEO (mirrors vula_pages.seo from migration 041).
    add column if not exists seo jsonb not null default '{}'::jsonb,
    -- Publish status, orthogonal to in_stock (availability) and archived (existing removal flag).
    add column if not exists status text not null default 'active'
        check (status in ('active', 'draft', 'unlisted', 'archived')),
    -- Option names/order for this product (e.g. ["Size","Colour"]) — Phase 4 variants prep.
    add column if not exists options jsonb;

-- Backfill status from the existing archived flag so nothing already live gets hidden.
update commerce_products set status = 'archived' where archived = true and status = 'active';
