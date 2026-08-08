-- Migration 122 — Fix two confirmed P0 bugs from the 2026-08-05 audit:
--
--   1) Invoice/order numbering race condition: `_next_invoice_number` and
--      `_next_order_display_id` both read the last number in Python and add 1,
--      so two concurrent checkouts/invoice creations can read the same "last"
--      value and mint the same number. Fixed with a tenant+doc-type scoped
--      counter table and a single atomic UPSERT ... RETURNING RPC.
--
--   2) Stock oversell: `create_order` never checked availability before
--      inserting an order — stock was only ever decremented later, at
--      payment confirmation, via `decrement_product_stock` /
--      `decrement_variant_stock`, which silently clamp at zero
--      (`GREATEST(0, ...)`). Two concurrent checkouts for the last unit of a
--      product would both succeed, and the shortfall would only surface as a
--      silent clamp with no error to either customer. Fixed by adding
--      `reserve_product_stock` / `reserve_variant_stock`: atomic,
--      conditional decrements that only succeed if enough stock exists
--      (untracked stock, i.e. NULL stock_quantity, is treated as unlimited
--      and always succeeds), called at order-creation time instead of at
--      payment time.

-- ── 1) Atomic document numbering ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_number_counters (
    tenant_id    TEXT    NOT NULL,
    counter_key  TEXT    NOT NULL,   -- e.g. 'invoice', 'quote', 'proforma', 'order'
    last_number  INTEGER NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, counter_key)
);

ALTER TABLE commerce_number_counters ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS commerce_number_counters_service_role ON commerce_number_counters;
CREATE POLICY commerce_number_counters_service_role ON commerce_number_counters
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

-- Seeds the counter from the existing table's current max so numbering
-- continues from where it left off rather than restarting at 1.
INSERT INTO commerce_number_counters (tenant_id, counter_key, last_number)
SELECT tenant_id, doc_type,
       COALESCE(MAX(NULLIF(regexp_replace(invoice_number, '.*-(\d+)$', '\1'), invoice_number)::INTEGER), 0)
FROM commerce_invoices
GROUP BY tenant_id, doc_type
ON CONFLICT (tenant_id, counter_key) DO NOTHING;

INSERT INTO commerce_number_counters (tenant_id, counter_key, last_number)
SELECT tenant_id, 'order',
       COALESCE(MAX(NULLIF(regexp_replace(display_id, '.*-(\d+)$', '\1'), display_id)::INTEGER), 0)
FROM commerce_orders
GROUP BY tenant_id
ON CONFLICT (tenant_id, counter_key) DO NOTHING;

-- Atomic "get and increment" — a single statement, so no read-then-write
-- gap exists for two concurrent callers to race inside.
CREATE OR REPLACE FUNCTION next_document_number(p_tenant_id TEXT, p_counter_key TEXT)
RETURNS INTEGER AS $$
DECLARE
    v_next INTEGER;
BEGIN
    INSERT INTO commerce_number_counters (tenant_id, counter_key, last_number)
    VALUES (p_tenant_id, p_counter_key, 1)
    ON CONFLICT (tenant_id, counter_key)
    DO UPDATE SET last_number = commerce_number_counters.last_number + 1,
                  updated_at = NOW()
    RETURNING last_number INTO v_next;
    RETURN v_next;
END;
$$ LANGUAGE plpgsql;

-- ── 2) Atomic stock reservation (prevents oversell) ───────────────────────

CREATE OR REPLACE FUNCTION reserve_product_stock(
    p_tenant_id TEXT,
    p_product_id UUID,
    p_qty INTEGER
) RETURNS BOOLEAN AS $$
DECLARE
    v_affected INTEGER;
BEGIN
    UPDATE commerce_products
    SET stock_quantity = stock_quantity - p_qty,
        in_stock = (stock_quantity - p_qty > 0),
        updated_at = NOW()
    WHERE id = p_product_id
      AND tenant_id = p_tenant_id
      -- NULL stock_quantity means stock isn't tracked for this product
      -- (e.g. made-to-order / service items) — always allow those through.
      AND (stock_quantity IS NULL OR stock_quantity >= p_qty);
    GET DIAGNOSTICS v_affected = ROW_COUNT;
    RETURN v_affected > 0;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION reserve_variant_stock(
    p_variant_id UUID,
    p_qty INTEGER
) RETURNS BOOLEAN AS $$
DECLARE
    v_affected INTEGER;
BEGIN
    UPDATE commerce_product_variants
    SET stock_quantity = stock_quantity - p_qty,
        in_stock = (stock_quantity - p_qty > 0),
        updated_at = NOW()
    WHERE id = p_variant_id
      AND (stock_quantity IS NULL OR stock_quantity >= p_qty);
    GET DIAGNOSTICS v_affected = ROW_COUNT;
    RETURN v_affected > 0;
END;
$$ LANGUAGE plpgsql;
