-- ============================================================
-- Vula Group — Supabase Migration 002: Commerce Tables
-- Run in: Supabase Dashboard > SQL Editor > New Query
-- Tenants: off-the-hook, awake-sa, kelp (and any future)
-- ============================================================

-- ── Products ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_products (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL,
    description     TEXT,
    category        TEXT NOT NULL
                        CHECK (category IN (
                            'fresh_fish',       -- fresh fish, sold per kg
                            'fresh_chicken',    -- fresh pasture-raised chicken, sold per kg
                            'frozen_chicken',   -- frozen pasture-raised chicken, sold per kg
                            'frozen_seafood',   -- frozen seafood, fixed pack price
                            'extras'            -- broths, collagen packs, other add-ons
                        )),
    sold_by         TEXT NOT NULL DEFAULT 'pack'
                        CHECK (sold_by IN ('kg', 'pack')),
    price_cents     INTEGER NOT NULL CHECK (price_cents > 0),
                    -- For sold_by='kg': price per kg in cents
                    -- For sold_by='pack': fixed pack price in cents
    pack_size       TEXT,           -- e.g. "1kg", "800g", "6 per pack"
    weight_grams    INTEGER,        -- typical/representative pack weight
    serves          TEXT,
    in_stock        BOOLEAN NOT NULL DEFAULT TRUE,
    stock_quantity  INTEGER,
    image_url       TEXT,
    is_daily_catch  BOOLEAN NOT NULL DEFAULT FALSE,  -- today's fresh catch highlight
    notes           TEXT,           -- e.g. "skin-on only", "subject to availability"
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, slug)
);

CREATE INDEX idx_commerce_products_tenant   ON commerce_products(tenant_id);
CREATE INDEX idx_commerce_products_category ON commerce_products(tenant_id, category);
CREATE INDEX idx_commerce_products_stock    ON commerce_products(tenant_id, in_stock);

ALTER TABLE commerce_products ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON commerce_products
    USING (tenant_id = current_setting('app.tenant_id', TRUE));

-- ── Carts ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_carts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    customer_phone  TEXT,
    customer_name   TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','converted','abandoned')),
    delivery_cents  INTEGER NOT NULL DEFAULT 8000,
    delivery_slot   TEXT CHECK (delivery_slot IN ('morning','afternoon','express')),
    delivery_address TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, session_id, status)
);

CREATE INDEX idx_commerce_carts_tenant  ON commerce_carts(tenant_id);
CREATE INDEX idx_commerce_carts_session ON commerce_carts(tenant_id, session_id);
CREATE INDEX idx_commerce_carts_phone   ON commerce_carts(customer_phone);

ALTER TABLE commerce_carts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON commerce_carts
    USING (tenant_id = current_setting('app.tenant_id', TRUE));

-- ── Cart Items ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_cart_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cart_id         UUID NOT NULL REFERENCES commerce_carts(id) ON DELETE CASCADE,
    product_id      UUID NOT NULL REFERENCES commerce_products(id),
    quantity        INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(cart_id, product_id)
);

CREATE INDEX idx_commerce_cart_items_cart ON commerce_cart_items(cart_id);

-- ── Orders ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_id          TEXT NOT NULL,            -- OTH-00001
    tenant_id           TEXT NOT NULL,
    cart_id             UUID REFERENCES commerce_carts(id),
    customer_phone      TEXT NOT NULL,
    customer_name       TEXT NOT NULL,
    customer_email      TEXT,
    delivery_address    TEXT NOT NULL,
    delivery_slot       TEXT CHECK (delivery_slot IN ('morning','afternoon','express')),
    delivery_notes      TEXT,
    subtotal_cents      INTEGER NOT NULL,
    delivery_cents      INTEGER NOT NULL DEFAULT 8000,
    total_cents         INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending_payment'
                            CHECK (status IN (
                                'pending_payment','paid','confirmed',
                                'packing','dispatched','delivered',
                                'cancelled','refunded'
                            )),
    channel             TEXT NOT NULL DEFAULT 'web'
                            CHECK (channel IN ('web','whatsapp')),
    yoco_checkout_id    TEXT,
    yoco_payment_id     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, display_id)
);

CREATE INDEX idx_commerce_orders_tenant  ON commerce_orders(tenant_id);
CREATE INDEX idx_commerce_orders_phone   ON commerce_orders(customer_phone);
CREATE INDEX idx_commerce_orders_status  ON commerce_orders(tenant_id, status);
CREATE INDEX idx_commerce_orders_yoco    ON commerce_orders(yoco_checkout_id);

ALTER TABLE commerce_orders ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON commerce_orders
    USING (tenant_id = current_setting('app.tenant_id', TRUE));

-- ── Order Items ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_order_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL REFERENCES commerce_orders(id) ON DELETE CASCADE,
    product_id      UUID REFERENCES commerce_products(id),
    product_name    TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    total_cents     INTEGER NOT NULL,
    catch_source    TEXT
);

CREATE INDEX idx_commerce_order_items_order ON commerce_order_items(order_id);

-- ── WhatsApp Conversations ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS whatsapp_conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL,
    phone_number    TEXT NOT NULL,
    customer_name   TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','completed','abandoned')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_wa_conv_tenant ON whatsapp_conversations(tenant_id);
CREATE INDEX idx_wa_conv_phone  ON whatsapp_conversations(phone_number);

-- ── WhatsApp Draft Orders (cart state in WhatsApp) ────────────────────────────

CREATE TABLE IF NOT EXISTS whatsapp_draft_orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL,
    conversation_id     UUID REFERENCES whatsapp_conversations(id),
    customer_phone      TEXT NOT NULL,
    items               JSONB NOT NULL DEFAULT '[]',
    total_cents         INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'building'
                            CHECK (status IN ('building','pending_payment','converted','abandoned')),
    cart_id             UUID REFERENCES commerce_carts(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_wa_draft_tenant ON whatsapp_draft_orders(tenant_id);
CREATE INDEX idx_wa_draft_phone  ON whatsapp_draft_orders(customer_phone);

-- ── Stock decrement function (race-safe) ─────────────────────────────────────

CREATE OR REPLACE FUNCTION decrement_product_stock(
    p_tenant_id TEXT,
    p_product_id UUID,
    p_delta INTEGER
) RETURNS VOID AS $$
BEGIN
    UPDATE commerce_products
    SET stock_quantity = GREATEST(0, stock_quantity - p_delta),
        in_stock = (stock_quantity - p_delta > 0),
        updated_at = NOW()
    WHERE id = p_product_id AND tenant_id = p_tenant_id;
END;
$$ LANGUAGE plpgsql;

-- ── Trigger: auto-update updated_at ──────────────────────────────────────────

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_commerce_products_updated_at
    BEFORE UPDATE ON commerce_products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_commerce_orders_updated_at
    BEFORE UPDATE ON commerce_orders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_commerce_carts_updated_at
    BEFORE UPDATE ON commerce_carts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
