-- ============================================================
-- Vula Group — Supabase Migration 001: tenants and documents
-- ============================================================
--
-- 3 Nov 2026: rewritten to the shape production actually has (checked against the live
-- schema), so a fresh database (local staging, a Supabase branch, CI) matches it. The first
-- version of this file was never what production ran: production's tables came from an
-- earlier hand-made schema, and this file's CREATE TABLE IF NOT EXISTS was a no-op there.
-- The old version made tenant_id a UUID (real tenant ids are slugs like 'digg-demo', so a
-- fresh database couldn't hold a real tenant), added a separate bigint id, a different
-- documents table, and a vula_admin_signups view production doesn't have.
--
-- Running this on production changes nothing: every statement is IF NOT EXISTS / replace.
-- The policies are service-role only. Production's own were open to every role until
-- migration 242 closed them.

-- ── Tenants ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS vula_tenants (
    tenant_id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    company_name            TEXT NOT NULL,
    industry                TEXT,
    staff_count             INTEGER,
    contact_name            TEXT,
    email                   TEXT UNIQUE,
    whatsapp                TEXT,
    plan                    TEXT NOT NULL DEFAULT 'starter',
    pain_points             JSONB DEFAULT '[]',
    status                  TEXT NOT NULL DEFAULT 'provisioning',
    trial_ends              TIMESTAMPTZ,
    workspace_url           TEXT,
    workspace_slug          TEXT UNIQUE,
    temp_password_hash      TEXT,
    paid                    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE vula_tenants ADD COLUMN IF NOT EXISTS paid BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE vula_tenants ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service_role_all" ON vula_tenants;
CREATE POLICY "service_role_all" ON vula_tenants
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE INDEX IF NOT EXISTS idx_tenants_email    ON vula_tenants(email);
CREATE INDEX IF NOT EXISTS idx_tenants_whatsapp ON vula_tenants(whatsapp);

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tenants_updated_at ON vula_tenants;
CREATE TRIGGER trg_tenants_updated_at
    BEFORE UPDATE ON vula_tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ── Documents (ingestion status per uploaded document) ──────────────────────

CREATE TABLE IF NOT EXISTS vula_documents (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES vula_tenants(tenant_id) ON DELETE CASCADE,
    doc_id          TEXT NOT NULL,
    filename        TEXT NOT NULL,
    file_type       TEXT,
    size_bytes      INTEGER,
    status          TEXT NOT NULL DEFAULT 'queued',
    chunks_stored   INTEGER DEFAULT 0,
    error           TEXT,
    uploaded_by     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, doc_id)
);

ALTER TABLE vula_documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service_role_all" ON vula_documents;
CREATE POLICY "service_role_all" ON vula_documents
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE INDEX IF NOT EXISTS idx_documents_tenant ON vula_documents(tenant_id);

DROP TRIGGER IF EXISTS trg_documents_updated_at ON vula_documents;
CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON vula_documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
