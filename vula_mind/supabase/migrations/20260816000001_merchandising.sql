-- Merchandising: bundles, cross-sells, cooking tips, real reviews.
-- (Applied to prod 16 Aug 2026 via Supabase MCP; kept here for the record.)
-- Popularity is computed from order data (no column needed).

ALTER TABLE commerce_products
  ADD COLUMN IF NOT EXISTS product_type TEXT NOT NULL DEFAULT 'single'
      CHECK (product_type IN ('single', 'bundle')),
  ADD COLUMN IF NOT EXISTS bundle_items JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS cooking_tips TEXT,
  ADD COLUMN IF NOT EXISTS related_ids JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS commerce_reviews (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      TEXT NOT NULL,
  product_id     UUID,
  order_id       UUID,
  customer_phone TEXT,
  customer_name  TEXT,
  rating         INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
  comment        TEXT,
  source         TEXT NOT NULL DEFAULT 'whatsapp',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reviews_tenant ON commerce_reviews(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reviews_product ON commerce_reviews(tenant_id, product_id);

ALTER TABLE commerce_reviews ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_reviews ON commerce_reviews;
CREATE POLICY tenant_isolation_reviews ON commerce_reviews
  USING (tenant_id = current_setting('request.jwt.claims', true)::json->>'tenant_id');

ALTER TABLE commerce_orders
  ADD COLUMN IF NOT EXISTS review_requested_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS review_rating INTEGER;
