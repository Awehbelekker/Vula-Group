# Backup & disaster recovery

Written 2026-09-18 as part of a go-live readiness pass — CLAUDE.md and the rest of the codebase
had zero documented backup/DR strategy before this. Covers the two stateful stores Vula depends
on: Supabase (Postgres + Storage) and Qdrant (per-tenant RAG vector collections).

## Supabase (Postgres + Storage)

**Confirmed via the Supabase API** (`mcp__Supabase__get_organization` on org
`cwyaxdvkaithirowyhwd`, project `vula-production` / `jzccetzmahpcoiqwlljm`, `eu-central-1`):
the organization is on the **Pro** plan.

- Pro includes **automated daily backups with 7-day retention** by default — no action needed,
  this is already active.
- **Point-in-time recovery (PITR)**, which brings RPO down from "up to 24h" to "seconds," is a
  separate **paid add-on** on top of Pro. Whether it's actually enabled for this project could
  **not be confirmed via the API** available to this session — the org/project metadata
  returned doesn't surface add-on state. **Action needed**: check Database → Backups in the
  Supabase dashboard for `vula-production` directly, and enable the PITR add-on if the
  business considers a 24h RPO on the tenant DB (orders, invoices, chat history, everything
  commerce-related) unacceptable — for a system this transactional, it likely is.
- **RTO**: not separately tested. Supabase's own restore process for Pro-tier daily backups is
  typically on the order of tens of minutes to a few hours depending on database size — treat
  as unverified until a real restore drill is run against a branch (`mcp__Supabase__create_branch`
  makes this cheap to test without touching production).
- Supabase Storage (signature images, filed documents, receipts) backup coverage was not
  separately verifiable via these tools either — flagged for the same manual dashboard check.

## Qdrant (per-tenant RAG knowledge base)

**Confirmed by code search**: no Qdrant snapshot/backup mechanism exists anywhere in this
codebase. `vula/integrations/metering.py`'s `snapshot_infra()` only records a **vector count**
for cost-metering purposes — it does not back up any actual vector data.

Per CLAUDE.md, Qdrant is **self-hosted** on the SA GPU box, reached via a Cloudflare-Access-
secured tunnel — not a managed service with its own backup story the way Supabase is.

**This was the single highest-severity finding in this review** — a tenant's entire RAG knowledge
base (everything ingested from WhatsApp documents/photos, email, ClickUp, OneDrive) lived only in
that one Qdrant instance, with zero backup. Unlike Supabase's structured records, most of this
content is **not trivially re-derivable**: a WhatsApp-photo-ingested receipt or a since-deleted
ClickUp task's content is gone for good if the Qdrant volume is lost, corrupted, or the GPU box
fails. Now covered by the daily snapshot job below.

**Fixed 2026-09-21.** This didn't actually need direct access to the GPU box — the backup job
runs *inside the already-deployed Railway backend*, which already has `QDRANT_BASE`/
`QDRANT_API_KEY` and reachability (the same process `vula/ingestion/pipeline.py`'s `QdrantStore`
uses for every RAG read/write today). Built as:

1. `vula/integrations/qdrant_backup.py`'s `backup_all_tenants()` — for every `vula_{tenant_id}`
   collection, calls Qdrant's own [snapshot API](https://qdrant.tech/documentation/concepts/snapshots/)
   (`POST /collections/{name}/snapshots` to create, `GET .../snapshots/{name}` to download,
   `DELETE .../snapshots/{name}` to remove Qdrant's own on-disk copy once uploaded), then uploads
   the snapshot bytes to a **private** Supabase Storage bucket, `qdrant-backups`, at
   `{tenant_id}/{utc-timestamp}.snapshot` (migration 171). Fail-open per tenant: one tenant's
   snapshot failing never stops the rest, mirroring `metering.py`'s `snapshot_infra()`.
2. Scheduled via `vula/api/server.py`'s `_qdrant_backup_loop`, registered in
   `_start_scheduled_job_tasks()` — daily, leader-only, same scheduler-lock mechanism guarding
   every other periodic job.
3. **Retention**: newest 7 snapshots kept per tenant (`_KEEP_PER_TENANT` in `qdrant_backup.py`) —
   a "recover from catastrophic loss" backstop, not a point-in-time audit trail (that's what
   Supabase's `reasoning_telemetry`/audit tables are for).
4. **Status visibility**: `vula_qdrant_backup_status` (migration 171) holds one row per tenant —
   `last_backup_at`, `last_backup_status` (`ok`/`error`), `last_backup_error`,
   `last_snapshot_path` — upserted after every run.

**Restore procedure** (manual, not yet drilled end-to-end — see the RPO/RTO caveat below):
1. Download the tenant's latest object from `qdrant-backups/{tenant_id}/` in Supabase Storage
   (service-role access only — the bucket is private) via the dashboard or
   `sb.storage.from_("qdrant-backups").download(path)`.
2. Upload it back into Qdrant with `POST /collections/{name}/snapshots/upload` (multipart file
   upload), where `name` is `vula_{tenant_id}` — this restores directly into that collection,
   overwriting its current contents. To restore into a fresh collection instead first (safer,
   lets you verify before cutting over), upload under a temporary name and use Qdrant's
   `PUT /collections/{name}/snapshots/recover` pointing at it, then rename/swap once verified.
3. Confirm point counts via `GET /collections/vula_{tenant_id}` before considering the tenant
   restored.

## RPO/RTO summary (current state)

| Store | RPO | RTO | Status |
|---|---|---|---|
| Supabase (Postgres, Storage) | ≤24h (daily backup) or seconds (if PITR add-on is enabled — unconfirmed) | Unverified, likely tens of minutes–hours | Needs one manual dashboard check |
| Qdrant (per-tenant RAG KB) | ≤24h (daily snapshot) | Manual, unverified — no restore drill run yet | Backup job live; restore drill still recommended |
