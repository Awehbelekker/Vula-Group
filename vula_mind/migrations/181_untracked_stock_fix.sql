-- 181_untracked_stock_fix.sql — checkout of an UNTRACKED product (stock_quantity IS NULL) failed.
--
-- 2026-09-27, found in production: every one of Off the Hook's 45 products is untracked, and
-- reserve_product_stock (migration 122) set in_stock = (stock_quantity - p_qty > 0), which is
-- NULL for an untracked row — in_stock is NOT NULL, so the UPDATE raised 23502 and every
-- WhatsApp/storefront checkout errored before the order was created (0 Off the Hook orders in
-- the 60 days before this fix). decrement_product_stock had the same fault on the paid-order
-- deduction, and decrement_variant_stock silently turned an untracked variant into a tracked
-- one at 0 (coalesce(NULL,0) - n), marking it out of stock after its first sale.
--
-- Rule, everywhere: an untracked row (stock_quantity IS NULL) stays untracked and keeps its
-- in_stock flag; a tracked row updates quantity and in_stock together. Idempotent.

CREATE OR REPLACE FUNCTION reserve_product_stock(p_tenant_id TEXT, p_product_id UUID, p_qty INTEGER)
RETURNS BOOLEAN LANGUAGE plpgsql SET search_path TO 'public' AS $$
DECLARE
    v_affected INTEGER;
BEGIN
    UPDATE commerce_products
    SET stock_quantity = CASE WHEN stock_quantity IS NULL THEN NULL ELSE stock_quantity - p_qty END,
        in_stock       = CASE WHEN stock_quantity IS NULL THEN in_stock ELSE stock_quantity - p_qty > 0 END,
        updated_at     = NOW()
    WHERE id = p_product_id
      AND tenant_id = p_tenant_id
      AND (stock_quantity IS NULL OR stock_quantity >= p_qty);
    GET DIAGNOSTICS v_affected = ROW_COUNT;
    RETURN v_affected > 0;
END;
$$;

CREATE OR REPLACE FUNCTION reserve_variant_stock(p_variant_id UUID, p_qty INTEGER)
RETURNS BOOLEAN LANGUAGE plpgsql SET search_path TO 'public' AS $$
DECLARE
    v_affected INTEGER;
BEGIN
    UPDATE commerce_product_variants
    SET stock_quantity = CASE WHEN stock_quantity IS NULL THEN NULL ELSE stock_quantity - p_qty END,
        in_stock       = CASE WHEN stock_quantity IS NULL THEN in_stock ELSE stock_quantity - p_qty > 0 END,
        updated_at     = NOW()
    WHERE id = p_variant_id
      AND (stock_quantity IS NULL OR stock_quantity >= p_qty);
    GET DIAGNOSTICS v_affected = ROW_COUNT;
    RETURN v_affected > 0;
END;
$$;

CREATE OR REPLACE FUNCTION decrement_product_stock(p_tenant_id TEXT, p_product_id UUID, p_delta INTEGER)
RETURNS VOID LANGUAGE plpgsql SET search_path TO 'public' AS $$
BEGIN
    UPDATE commerce_products
    SET stock_quantity = CASE WHEN stock_quantity IS NULL THEN NULL ELSE GREATEST(0, stock_quantity - p_delta) END,
        in_stock       = CASE WHEN stock_quantity IS NULL THEN in_stock ELSE stock_quantity - p_delta > 0 END,
        updated_at     = NOW()
    WHERE id = p_product_id AND tenant_id = p_tenant_id;
END;
$$;

CREATE OR REPLACE FUNCTION decrement_variant_stock(p_variant_id UUID, p_delta INTEGER)
RETURNS VOID LANGUAGE plpgsql SET search_path TO 'public' AS $$
BEGIN
    UPDATE commerce_product_variants
    SET stock_quantity = CASE WHEN stock_quantity IS NULL THEN NULL ELSE GREATEST(0, stock_quantity - p_delta) END,
        in_stock       = CASE WHEN stock_quantity IS NULL THEN in_stock ELSE stock_quantity - p_delta > 0 END,
        updated_at     = NOW()
    WHERE id = p_variant_id;
END;
$$;
