"""Read-only health report for every tenant knowledge base in Qdrant (2026-10-16, KB retrieval
phase 1 — docs/kb-retrieval-audit.md).

    python tools/kb_stats.py                      # every vula_* collection
    python tools/kb_stats.py --tenant gerflor     # one tenant
    python tools/kb_stats.py --json report.json   # also save the numbers

What it reports, per collection — counts and lengths only, never chunk text (POPIA):

  * the Qdrant server version (C3: pin docker-compose/DEPLOYMENT.md at or above it);
  * chunk length distribution in characters and estimated tokens (chars/4), and the share of
    chunks longer than the old 900-character prompt cut (C1);
  * points vs distinct chunk ids, legacy 32-bit ids vs uuid ids, and chunk_index GAPS per
    document — a gap is a chunk that was written and is no longer there, which is what a 32-bit
    id collision does (the second chunk overwrites the first). A gap whose legacy id is now held
    by a different chunk is a CONFIRMED collision (C2);
  * how many points carry an `audience` (C5: the backfill still to do).

Uses QDRANT_BASE / QDRANT_API_KEY from vula_mind/.env like the app. Only GET and scroll calls.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _pct(vals: List[int], p: float) -> Optional[int]:
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, math.ceil(p * len(s)) - 1))]


def expected_collisions(n: int) -> float:
    """Expected number of colliding pairs among n random 32-bit ids."""
    return n * (n - 1) / 2 / 2 ** 32


def analyse(points: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure: the report for one collection's points ({"id", "payload"})."""
    from vula.ingestion.pipeline import legacy_point_id
    lengths = [len(str((p.get("payload") or {}).get("text") or "")) for p in points]
    by_id = {p["id"]: (p.get("payload") or {}) for p in points}
    chunk_ids = [pl.get("chunk_id") for pl in by_id.values() if pl.get("chunk_id")]
    legacy = sum(1 for i in by_id if isinstance(i, int))
    docs: Dict[str, set] = {}
    for pl in by_id.values():
        if pl.get("doc_id") is not None and isinstance(pl.get("chunk_index"), int):
            docs.setdefault(str(pl["doc_id"]), set()).add(pl["chunk_index"])
    gaps, confirmed = 0, 0
    docs_with_gaps = 0
    for doc_id, idx in docs.items():
        missing = [i for i in range(max(idx) + 1) if i not in idx]
        if missing:
            docs_with_gaps += 1
        gaps += len(missing)
        for i in missing:
            holder = by_id.get(legacy_point_id(f"{doc_id}_{i}"))
            if holder is not None and holder.get("chunk_id") != f"{doc_id}_{i}":
                confirmed += 1
    audiences: Dict[str, int] = {}
    for pl in by_id.values():
        a = str(pl.get("audience") or "(none)")
        audiences[a] = audiences.get(a, 0) + 1
    source_types: Dict[str, int] = {}
    for pl in by_id.values():
        s = str(pl.get("source_type") or "(none)")
        source_types[s] = source_types.get(s, 0) + 1
    return {
        "points": len(points), "distinct_chunk_ids": len(set(chunk_ids)),
        "duplicate_chunk_ids": len(chunk_ids) - len(set(chunk_ids)),
        "legacy_int_ids": legacy, "uuid_ids": len(by_id) - legacy,
        "expected_legacy_collisions": round(expected_collisions(legacy), 4),
        "documents": len(docs), "documents_with_gaps": docs_with_gaps, "missing_chunks": gaps,
        "confirmed_collisions": confirmed,
        "chars": {"min": min(lengths) if lengths else None, "p50": _pct(lengths, 0.5),
                  "p90": _pct(lengths, 0.9), "max": max(lengths) if lengths else None},
        "est_tokens": {"p50": (_pct(lengths, 0.5) or 0) // 4, "p90": (_pct(lengths, 0.9) or 0) // 4},
        "over_900_chars_pct": round(100 * sum(1 for n in lengths if n > 900) / len(lengths), 1) if lengths else None,
        "audience": audiences, "source_type": source_types,
    }


async def _scroll_all(client, base: str, name: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    offset = None
    while True:
        body: Dict[str, Any] = {"limit": 256, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        r = await client.post(f"{base}/collections/{name}/points/scroll", json=body)
        r.raise_for_status()
        res = r.json().get("result", {})
        out += [{"id": p["id"], "payload": p.get("payload") or {}} for p in res.get("points", [])]
        offset = res.get("next_page_offset")
        if offset is None:
            return out


async def main(tenant: Optional[str], json_out: Optional[str]) -> int:
    import httpx
    from vula.ingestion.pipeline import QdrantStore
    store = QdrantStore()
    report: Dict[str, Any] = {"qdrant_base_host": re.sub(r"^https?://", "", store.base).split("/")[0]}
    async with httpx.AsyncClient(timeout=60.0, headers=store._headers()) as client:
        try:
            report["qdrant_version"] = (await client.get(f"{store.base}/")).json().get("version")
        except Exception as exc:  # noqa: BLE001
            report["qdrant_version"] = f"unknown ({type(exc).__name__})"
        if tenant:
            names = [store._collection_name(tenant)]
        else:
            r = await client.get(f"{store.base}/collections")
            r.raise_for_status()
            names = sorted(c["name"] for c in r.json()["result"]["collections"] if c["name"].startswith("vula_"))
        report["collections"] = {}
        for name in names:
            try:
                report["collections"][name] = analyse(await _scroll_all(client, store.base, name))
            except Exception as exc:  # noqa: BLE001
                report["collections"][name] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    print(f"Qdrant {report['qdrant_version']} at {report['qdrant_base_host']}")
    for name, s in report["collections"].items():
        if "error" in s:
            print(f"{name}: {s['error']}")
            continue
        print(f"{name}: {s['points']} points ({s['legacy_int_ids']} legacy ids, {s['uuid_ids']} uuid), "
              f"{s['distinct_chunk_ids']} chunk ids, {s['missing_chunks']} missing chunks in "
              f"{s['documents_with_gaps']}/{s['documents']} docs, {s['confirmed_collisions']} confirmed "
              f"collisions (expected {s['expected_legacy_collisions']}); chars p50 {s['chars']['p50']} "
              f"p90 {s['chars']['p90']} max {s['chars']['max']} (~{s['est_tokens']['p50']}/"
              f"{s['est_tokens']['p90']} tokens), {s['over_900_chars_pct']}% over 900; audience {s['audience']}")
    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tenant")
    ap.add_argument("--json")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.tenant, a.json)))
