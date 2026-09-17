-- 164_research_promotion_and_network_sharing.sql
--
-- Depth pass (product owner, 2026-09-17): grow Vula's own shared knowledge base from its own
-- research, plus let a tenant opt in to sharing its own reviewed knowledge with OTHER tenants
-- ("maybe if we ask permission, if the client or tenant is willing to share, could be great").
--
-- Two additions, kept deliberately separate:
--
-- 1. vula_learned_answers gets a promotion record (mirrors vula_escalations' answered_at/
--    approved_by pattern already in this table) so a row can't be double-promoted into a shared
--    KB collection, and a share_scope so the master-admin promotion queue (vula/api/master.py)
--    can tell an already-general row (source='web_research', Vula's own research — see
--    core/verification.py's two-signal accuracy gate) from a tenant-specific one a tenant chose
--    to share into the cross-tenant network (source='owner_correction' + share_scope='network',
--    only ever set when that tenant has opted in — see (2) below). Neither is auto-promoted:
--    both still land here as reviewable candidates, promoted by a human tap.
--
-- 2. vula_tenant_config gets share_knowledge_with_network — the opt-in itself. Default false:
--    a tenant's approved answers stay internal (share_scope='internal', the existing default
--    behaviour) unless the tenant explicitly turns this on. Read by vula/api/master.py's
--    promote endpoint before it will ever move that tenant's content into the shared
--    "vula_network" collection other tenants' reasoning.py/architecture_planning.py queries.

ALTER TABLE vula_learned_answers
    ADD COLUMN IF NOT EXISTS promoted_to_shared_kb_at timestamptz,
    ADD COLUMN IF NOT EXISTS promoted_by               text,
    ADD COLUMN IF NOT EXISTS share_scope               text NOT NULL DEFAULT 'internal';

ALTER TABLE vula_tenant_config
    ADD COLUMN IF NOT EXISTS share_knowledge_with_network boolean NOT NULL DEFAULT false;

-- The master promotion queue's own read: approved rows and pending web_research rows, not yet
-- promoted, oldest first so nothing sits forever un-reviewed.
CREATE INDEX IF NOT EXISTS idx_learned_answers_promotable
    ON vula_learned_answers (created_at)
    WHERE promoted_to_shared_kb_at IS NULL
      AND (status = 'approved' OR (status = 'pending' AND source = 'web_research'));
