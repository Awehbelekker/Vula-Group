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

**This is the single highest-severity finding in this review.** A tenant's entire RAG knowledge
base — everything ingested from WhatsApp documents/photos, email, ClickUp, OneDrive — lives only
in that one Qdrant instance, with **zero backup**. Unlike Supabase's structured records, most of
this content is **not trivially re-derivable**: a WhatsApp-photo-ingested receipt or a since-
deleted ClickUp task's content is gone for good if the Qdrant volume is lost, corrupted, or the
GPU box fails.

**Not fixed in this pass** — this session doesn't have direct access to the GPU box to build and
verify a real snapshot job against it, and speculatively writing an untestable backup script
would be worse than flagging the gap honestly. Recommended follow-up, sequenced as its own small
project once someone has box access:

1. A periodic job calling Qdrant's own [snapshot API](https://qdrant.tech/documentation/concepts/snapshots/)
   per collection (`POST /collections/{name}/snapshots`), uploading the resulting snapshot file
   to Supabase Storage (already the pattern this codebase uses for other generated artifacts —
   see `_upload_to_storage` in `vula/api/whatsapp.py`) or another S3-compatible bucket.
2. Wire it into the existing scheduled-loop pattern (`vula/api/server.py`'s
   `_start_scheduled_job_tasks()`), e.g. daily, leader-only (reuse the scheduler-lock mechanism
   already guarding the other periodic jobs).
3. Retention: a handful of recent daily snapshots is enough — this is a "recover from
   catastrophic loss" backstop, not a point-in-time audit trail (that's what Supabase's
   `reasoning_telemetry`/audit tables are for).
4. Document the restore procedure once built (which collection maps to which tenant —
   `vula_{tenant_id}` — and the exact Qdrant restore-from-snapshot steps) here, replacing this
   section.

## RPO/RTO summary (current state)

| Store | RPO | RTO | Status |
|---|---|---|---|
| Supabase (Postgres, Storage) | ≤24h (daily backup) or seconds (if PITR add-on is enabled — unconfirmed) | Unverified, likely tens of minutes–hours | Needs one manual dashboard check |
| Qdrant (per-tenant RAG KB) | **No backup — unbounded data loss on failure** | N/A | **Real gap, follow-up project needed** |
