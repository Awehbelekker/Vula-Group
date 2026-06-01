-- ============================================================
-- Vula Group — Migration 005: Supabase Storage + Auth Setup
-- Run in Supabase Dashboard → SQL Editor → New Query → Run
-- ============================================================

-- ── Supabase Storage: product-images bucket ──────────────────────────────────
-- Run these via Supabase Dashboard → Storage → New bucket:
--   Name: product-images
--   Public: YES
--
-- OR uncomment below if your Supabase version supports it:
-- INSERT INTO storage.buckets (id, name, public)
--   VALUES ('product-images', 'product-images', true)
-- ON CONFLICT (id) DO NOTHING;

-- ── RLS policy: tenants can upload to their own folder ───────────────────────
-- Folder structure: product-images/{tenant_id}/{filename}

-- Allow authenticated users to upload to their tenant folder
-- (run these in Supabase Dashboard → Storage → product-images bucket → Policies)
--
-- Policy name: "Authenticated upload to own tenant folder"
-- Allowed operations: INSERT
-- Target roles: authenticated
-- Policy definition:
--   (storage.foldername(name))[1] = auth.uid()::text
--   OR auth.role() = 'service_role'
--
-- Policy name: "Public read"
-- Allowed operations: SELECT
-- Target roles: (public)
-- Policy definition: true

-- ── Auth: Vula Commerce tenant users ─────────────────────────────────────────
-- Table maps Supabase Auth users to Vula Commerce tenants

CREATE TABLE IF NOT EXISTS vula_tenant_users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    tenant_id       TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'owner'
                        CHECK (role IN ('owner', 'staff', 'master')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_users_user_tenant
    ON vula_tenant_users(user_id, tenant_id);

CREATE INDEX IF NOT EXISTS idx_tenant_users_tenant
    ON vula_tenant_users(tenant_id);

-- RLS
ALTER TABLE vula_tenant_users ENABLE ROW LEVEL SECURITY;

-- Users can see their own tenant assignments
CREATE POLICY "users_see_own" ON vula_tenant_users
    FOR SELECT USING (auth.uid() = user_id);

-- Only service role can manage assignments
CREATE POLICY "service_role_manage" ON vula_tenant_users
    FOR ALL USING (auth.role() = 'service_role');

-- ── Seed: Ian as master admin ─────────────────────────────────────────────────
-- After creating Ian's Supabase Auth account (see README), run:
-- INSERT INTO vula_tenant_users (user_id, tenant_id, role)
-- VALUES ('<ian-supabase-user-id>', 'master', 'master');

-- ── Seed: Off the Hook owner ──────────────────────────────────────────────────
-- After creating the client's Supabase Auth account:
-- INSERT INTO vula_tenant_users (user_id, tenant_id, role)
-- VALUES ('<client-supabase-user-id>', 'off-the-hook', 'owner');
