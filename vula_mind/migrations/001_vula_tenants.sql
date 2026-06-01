-- ============================================================
-- Vula Group — Supabase Migration 001
-- Run in: Supabase Dashboard > SQL Editor > New Query
-- ============================================================

-- ── Tenants ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS vula_tenants (
    id                      BIGSERIAL PRIMARY KEY,
    tenant_id               UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    company_name            TEXT NOT NULL,
    industry                TEXT,
    staff_count             INTEGER,
    contact_name            TEXT NOT NULL,
    email                   TEXT NOT NULL UNIQUE,
    whatsapp                TEXT,
    plan                    TEXT NOT NULL DEFAULT 'starter'
                                CHECK (plan IN ('starter', 'growth', 'business')),
    pain_points             JSONB DEFAULT '[]',
    status                  TEXT NOT NULL DEFAULT 'provisioning'
                                CHECK (status IN ('provisioning', 'active', 'suspended', 'cancelled')),
    trial_ends              TIMESTAMPTZ,
    workspace_url           TEXT,
    workspace_slug          TEXT UNIQUE,
    temp_password_hash      TEXT,
    payfast_subscription_id TEXT,
    paid                    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- If you are upgrading from an earlier schema, add the column in place:
ALTER TABLE vula_tenants ADD COLUMN IF NOT EXISTS paid BOOLEAN NOT NULL DEFAULT FALSE;

-- Row-level security — each tenant only sees their own record
ALTER TABLE vula_tenants ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Tenants read own record"
    ON vula_tenants FOR SELECT
    USING (auth.uid()::text = tenant_id::text);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_vula_tenants_email     ON vula_tenants(email);
CREATE INDEX IF NOT EXISTS idx_vula_tenants_status    ON vula_tenants(status);
CREATE INDEX IF NOT EXISTS idx_vula_tenants_created   ON vula_tenants(created_at DESC);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER vula_tenants_updated_at
    BEFORE UPDATE ON vula_tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ── Documents ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS vula_documents (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           UUID REFERENCES vula_tenants(tenant_id) ON DELETE CASCADE,
    filename            TEXT NOT NULL,
    storage_path        TEXT,
    content_type        TEXT,
    size_bytes          INTEGER,
    ingestion_status    TEXT NOT NULL DEFAULT 'queued'
                            CHECK (ingestion_status IN ('queued', 'processing', 'done', 'failed')),
    chunk_count         INTEGER,
    qdrant_collection   TEXT,      -- vula_{tenant_id}
    error_message       TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE vula_documents ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Tenants read own documents"
    ON vula_documents FOR SELECT
    USING (
        auth.uid()::text = (
            SELECT tenant_id::text FROM vula_tenants WHERE tenant_id = vula_documents.tenant_id
        )
    );

CREATE INDEX IF NOT EXISTS idx_vula_docs_tenant   ON vula_documents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_vula_docs_status   ON vula_documents(ingestion_status);

CREATE TRIGGER vula_documents_updated_at
    BEFORE UPDATE ON vula_documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ── Admin view (service role only) ───────────────────────────────────────────

CREATE OR REPLACE VIEW vula_admin_signups AS
SELECT
    t.company_name,
    t.contact_name,
    t.email,
    t.whatsapp,
    t.plan,
    t.industry,
    t.status,
    t.trial_ends,
    t.created_at,
    COUNT(d.id) AS document_count
FROM vula_tenants t
LEFT JOIN vula_documents d ON d.tenant_id = t.tenant_id
GROUP BY t.id
ORDER BY t.created_at DESC;
