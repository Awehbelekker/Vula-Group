"""
vula/integrations/qdrant_backup.py — periodic per-tenant Qdrant collection backup.

docs/dr.md flagged Qdrant (self-hosted per-tenant RAG vector store, per CLAUDE.md reached via the
SA GPU box's Cloudflare tunnel) as having zero backup mechanism — the highest-severity finding in
the go-live readiness DR review. This module snapshots each tenant's Qdrant collection via
Qdrant's own snapshot API, uploads the snapshot file to a private Supabase Storage bucket, prunes
old snapshots, and records status per tenant (migration 171).

Runs inside the already-deployed backend, which already has QDRANT_BASE/QDRANT_API_KEY
reachability (the same process vula/ingestion/pipeline.py's QdrantStore uses for every RAG
read/write) — scheduled daily via vula/api/server.py's _qdrant_backup_loop. Fail-open per tenant
throughout: one tenant's failure must never abort the rest, mirroring metering.py's
snapshot_infra() shape.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_KEEP_PER_TENANT = 7  # a catastrophic-loss backstop, not a point-in-time audit trail
_BUCKET = "qdrant-backups"


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


def _qdrant_headers() -> dict:
    import os
    key = os.environ.get("QDRANT_API_KEY") or ""
    return {"api-key": key} if key else {}


def _list_tenant_collections(cols: dict) -> list:
    """Given a GET /collections response body, return [(collection_name, tenant_id), ...] for
    every real tenant collection — same vula_-prefix filter + training-collection exclusion as
    metering.py's snapshot_infra()."""
    out = []
    for c in cols.get("result", {}).get("collections", []):
        name = c["name"]
        if not name.startswith("vula_") or name == "vula_vula_training":
            continue
        tenant = name[len("vula_"):].replace("_", "-")
        out.append((name, tenant))
    return out


async def _snapshot_one_collection(client, qbase: str, headers: dict, name: str) -> bytes:
    """Create a Qdrant snapshot, download its bytes, then delete Qdrant's own on-disk copy —
    always, even if the download raised — so a flaky download doesn't leave orphaned snapshots
    accumulating on Qdrant's disk."""
    resp = await client.post(f"{qbase}/collections/{name}/snapshots", headers=headers)
    resp.raise_for_status()
    snap_name = resp.json()["result"]["name"]
    try:
        dl = await client.get(f"{qbase}/collections/{name}/snapshots/{snap_name}", headers=headers)
        dl.raise_for_status()
        return dl.content
    finally:
        try:
            await client.delete(f"{qbase}/collections/{name}/snapshots/{snap_name}", headers=headers)
        except Exception as exc:
            logger.debug("qdrant snapshot cleanup skipped for %s/%s: %s", name, snap_name, exc)


def _upload_and_prune(tenant_id: str, data: bytes) -> str:
    """Blocking (Supabase storage-py). Upload the snapshot to the private bucket, then prune to
    the newest _KEEP_PER_TENANT objects under this tenant's prefix. Returns the uploaded path."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = f"{tenant_id}/{stamp}.snapshot"
    sb = _client()
    try:
        sb.storage.create_bucket(_BUCKET, options={"public": False})
    except Exception:
        pass  # already exists (created by migration 171)
    sb.storage.from_(_BUCKET).upload(path, data, {"content-type": "application/octet-stream"})

    existing = sb.storage.from_(_BUCKET).list(tenant_id) or []
    existing.sort(key=lambda o: o.get("created_at") or o.get("name") or "", reverse=True)
    stale = existing[_KEEP_PER_TENANT:]
    if stale:
        sb.storage.from_(_BUCKET).remove([f"{tenant_id}/{o['name']}" for o in stale])
    return path


def _record_status(tenant_id: str, *, ok: bool, error: str = "", path: str = "") -> None:
    try:
        _client().table("vula_qdrant_backup_status").upsert({
            "tenant_id": tenant_id,
            "last_backup_at": datetime.now(timezone.utc).isoformat(),
            "last_backup_status": "ok" if ok else "error",
            "last_backup_error": error[:500],
            "last_snapshot_path": path,
            "updated_at": "now()",
        }, on_conflict="tenant_id").execute()
    except Exception as exc:
        logger.debug("qdrant backup status upsert skipped: %s", exc)


async def backup_all_tenants() -> int:
    """Snapshot every tenant's Qdrant collection, upload + prune, record status. Fail-open per
    tenant — one tenant's failure never aborts the rest. Returns count of successful backups."""
    import asyncio
    import os
    import httpx

    qbase = (os.environ.get("QDRANT_BASE") or "").rstrip("/")
    if not qbase:
        return 0
    headers = _qdrant_headers()
    n = 0
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(f"{qbase}/collections", headers=headers)
            resp.raise_for_status()
            collections = _list_tenant_collections(resp.json())
            for name, tenant_id in collections:
                try:
                    data = await _snapshot_one_collection(client, qbase, headers, name)
                    path = await asyncio.to_thread(_upload_and_prune, tenant_id, data)
                    _record_status(tenant_id, ok=True, path=path)
                    n += 1
                except Exception as exc:
                    logger.warning("Qdrant backup failed for tenant %s: %s", tenant_id, exc)
                    _record_status(tenant_id, ok=False, error=str(exc))
    except Exception as exc:
        logger.warning("Qdrant backup: collection listing failed: %s", exc)
    return n
