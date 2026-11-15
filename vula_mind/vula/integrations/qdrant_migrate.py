"""
vula/integrations/qdrant_migrate.py — copy every Qdrant collection to a new cluster (13 Nov 2026).

The knowledge base ran on Qdrant Cloud in AWS sa-east-1 (São Paulo), while the database is in
Frankfurt and the API moved to Amsterdam ("move everything to the EU"). Qdrant Cloud can't move a
cluster between regions, so a new EU cluster is filled from this one:

  QDRANT_MIGRATE_TO_BASE / QDRANT_MIGRATE_TO_API_KEY name the new cluster. After boot, on the
  scheduler leader, each collection on the current cluster (QDRANT_BASE) is snapshotted, the
  snapshot uploaded to the new cluster (priority=snapshot replaces what's there, so a re-run is
  safe), and the point counts compared. One telemetry row per collection (system
  'qdrant-migrate': name, points on each side, seconds, error) — never any content.

Nothing is deleted from the current cluster. Switching over is a separate step: point QDRANT_BASE
and QDRANT_API_KEY at the new cluster; once they match the target, this does nothing.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

SYSTEM = "qdrant-migrate"
_running = False


def _src() -> str:
    return (settings.qdrant_base or "").rstrip("/")


def _dst() -> str:
    return (settings.qdrant_migrate_to_base or "").rstrip("/")


def _headers(key: Optional[str]) -> dict:
    return {"api-key": key} if key else {}


def wanted() -> bool:
    """A target is set and it isn't the cluster already in use."""
    return bool(_dst()) and _dst() != _src()


async def _points(client: httpx.AsyncClient, base: str, key: Optional[str], name: str) -> Optional[int]:
    r = await client.get(f"{base}/collections/{name}", headers=_headers(key))
    if r.status_code == 404:
        return None
    r.raise_for_status()
    res = r.json().get("result") or {}
    return int(res.get("points_count") or 0)


async def _copy_one(client: httpx.AsyncClient, name: str) -> Dict[str, Any]:
    from vula.integrations.qdrant_backup import _snapshot_one_collection
    t0 = time.monotonic()
    src_key, dst_key = settings.qdrant_api_key, settings.qdrant_migrate_to_api_key
    out: Dict[str, Any] = {"collection": name, "ok": False, "error": None}
    try:
        out["source_points"] = await _points(client, _src(), src_key, name)
        data = await _snapshot_one_collection(client, _src(), _headers(src_key), name)
        out["bytes"] = len(data)
        up = await client.post(
            f"{_dst()}/collections/{name}/snapshots/upload",
            params={"priority": "snapshot", "wait": "true"},
            headers=_headers(dst_key),
            files={"snapshot": (f"{name}.snapshot", data, "application/octet-stream")},
        )
        up.raise_for_status()
        out["target_points"] = await _points(client, _dst(), dst_key, name)
        out["ok"] = out["target_points"] == out["source_points"]
        if not out["ok"]:
            out["error"] = (f"point counts differ: source {out['source_points']}, "
                            f"target {out['target_points']}")
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"[:300]
    out["seconds"] = int(time.monotonic() - t0)
    return out


def _record(row: Dict[str, Any]) -> None:
    try:
        from core.reasoning_telemetry import emit
        emit(system=SYSTEM, task="copy", outcome="ok" if row.get("ok") else "failed",
             reason=row.get("error"), extra={k: v for k, v in row.items() if k not in ("ok", "error")})
    except Exception:  # noqa: BLE001
        pass


async def migrate_all(client: Optional[httpx.AsyncClient] = None) -> Dict[str, Dict[str, Any]]:
    """Copy every collection on the current cluster to the target. {name: result}."""
    if not wanted():
        return {}
    own = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=600.0, write=600.0))
    results: Dict[str, Dict[str, Any]] = {}
    try:
        r = await client.get(f"{_src()}/collections", headers=_headers(settings.qdrant_api_key))
        r.raise_for_status()
        names = sorted(c["name"] for c in (r.json().get("result") or {}).get("collections") or [])
        logger.warning("Qdrant migration: copying %d collection(s) to the new cluster", len(names))
        for name in names:
            row = await _copy_one(client, name)
            results[name] = row
            _record(row)
            logger.warning("Qdrant migration %s: %s (%s → %s points, %ss)%s", name,
                           "ok" if row["ok"] else "FAILED", row.get("source_points"),
                           row.get("target_points"), row.get("seconds"),
                           f" — {row['error']}" if row.get("error") else "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Qdrant migration: couldn't list collections: %s", exc)
    finally:
        if own:
            await client.aclose()
    return results


async def run_once_after_boot(delay_s: float = 120.0) -> None:
    """One copy per boot, while a target other than the current cluster is set."""
    global _running
    if _running or not wanted():
        return
    _running = True
    try:
        await asyncio.sleep(delay_s)
        res = await migrate_all()
        ok = sum(1 for r in res.values() if r.get("ok"))
        logger.warning("Qdrant migration done: %d of %d collections copied and checked", ok, len(res))
    finally:
        _running = False
