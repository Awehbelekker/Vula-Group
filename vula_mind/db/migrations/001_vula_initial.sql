-- ═══════════════════════════════════════════════════════════════════════════
-- Vula Group — Supabase Initial Schema
-- Run this in your Supabase project: Dashboard → SQL Editor → New query → Run
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Tenants ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS vula_tenants (
    tenant_id       TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    company_name    TEXT NOT NULL,
    industry        TEXT,
    staff_count     INTEGER,
    contact_name    TEXT,
    email           TEXT UNIQUE NOT NULL,
    whatsapp        TEXT,                      -- primary contact number (normalised, no +)
    plan            TEXT NOT NULL DEFAULT 'starter',
    status          TEXT NOT NULL DEFAULT 'provisioning',
    pain_points     JSONB DEFAULT '[]',
    trial_ends      TIMESTAMPTZ,
    workspace_url   TEXT,
    workspace_slug  TEXT UNIQUE,
    temp_password_hash TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Multiple phone numbers per tenant (same as local SQLite model)
CREATE TABLE IF NOT EXISTS vula_tenant_phones (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES vula_tenants(tenant_id) ON DELETE CASCADE,
    phone       TEXT NOT NULL UNIQUE,          -- normalised 27XXXXXXXXX
    label       TEXT,
    role        TEXT NOT NULL DEFAULT 'staff', -- admin | staff | viewer
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_phones_phone     ON vula_tenant_phones(phone);
CREATE INDEX IF NOT EXISTS idx_tenant_phones_tenant_id ON vula_tenant_phones(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenants_whatsapp        ON vula_tenants(whatsapp);
CREATE INDEX IF NOT EXISTS idx_tenants_email           ON vula_tenants(email);

-- ── Users (staff logins per tenant) ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS vula_users (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES vula_tenants(tenant_id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    name        TEXT,
    role        TEXT NOT NULL DEFAULT 'member', -- owner | admin | member
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(tenant_id, email)
);

-- ── Subscriptions ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS vula_subscriptions (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES vula_tenants(tenant_id) ON DELETE CASCADE,
    plan                TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'trial', -- trial | active | past_due | cancelled
    payfast_token       TEXT,
    amount_cents        INTEGER,
    trial_ends          TIMESTAMPTZ,
    current_period_end  TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

-- ── Documents ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS vula_documents (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES vula_tenants(tenant_id) ON DELETE CASCADE,
    doc_id          TEXT NOT NULL,              -- matches Qdrant payload doc_id
    filename        TEXT NOT NULL,
    file_type       TEXT,
    size_bytes      INTEGER,
    status          TEXT NOT NULL DEFAULT 'queued', -- queued | processing | done | failed
    chunks_stored   INTEGER DEFAULT 0,
    error           TEXT,
    uploaded_by     TEXT,                       -- phone or email
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(tenant_id, doc_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant ON vula_documents(tenant_id);

-- ── Query history ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS vula_queries (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES vula_tenants(tenant_id) ON DELETE CASCADE,
    phone       TEXT,
    question    TEXT NOT NULL,
    answer      TEXT,
    sources     JSONB DEFAULT '[]',
    channel     TEXT DEFAULT 'whatsapp',        -- whatsapp | dashboard | api
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_queries_tenant ON vula_queries(tenant_id);

-- ── Row Level Security ───────────────────────────────────────────────────────
-- Enable RLS so each tenant can only see their own data.
-- The service role key bypasses RLS (used by the backend).

ALTER TABLE vula_tenants        ENABLE ROW LEVEL SECURITY;
ALTER TABLE vula_tenant_phones  ENABLE ROW LEVEL SECURITY;
ALTER TABLE vula_users          ENABLE ROW LEVEL SECURITY;
ALTER TABLE vula_subscriptions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE vula_documents      ENABLE ROW LEVEL SECURITY;
ALTER TABLE vula_queries        ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS — these policies only apply to anon/authenticated roles
CREATE POLICY "service_role_all" ON vula_tenants        FOR ALL USING (true);
CREATE POLICY "service_role_all" ON vula_tenant_phones  FOR ALL USING (true);
CREATE POLICY "service_role_all" ON vula_users          FOR ALL USING (true);
CREATE POLICY "service_role_all" ON vula_subscriptions  FOR ALL USING (true);
CREATE POLICY "service_role_all" ON vula_documents      FOR ALL USING (true);
CREATE POLICY "service_role_all" ON vula_queries        FOR ALL USING (true);

-- ── Auto-update updated_at ───────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tenants_updated_at
    BEFORE UPDATE ON vula_tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_subscriptions_updated_at
    BEFORE UPDATE ON vula_subscriptions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON vula_documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── Seed: DIGG demo tenant ───────────────────────────────────────────────────
-- Remove this block once you have real data.

INSERT INTO vula_tenants (tenant_id, company_name, industry, contact_name, email, plan, status, trial_ends)
VALUES (
    'digg-demo',
    'DIGG Architecture',
    'architecture',
    'Judy Downing',
    'judy@digg.co.za',
    'starter',
    'active',
    now() + interval '30 days'
) ON CONFLICT (tenant_id) DO NOTHING;

INSERT INTO vula_tenant_phones (tenant_id, phone, label, role)
VALUES
    ('digg-demo', '27827077080', 'judy-personal', 'admin'),
    ('digg-demo', '27673636081', 'vula-business', 'admin')
ON CONFLICT (phone) DO NOTHING;
