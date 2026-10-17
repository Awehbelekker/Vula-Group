"""Tag the KB chunks stored before 2026-10-16 with an audience (KB retrieval 1b, 2026-10-17).

Why: chunks written since KB retrieval C5 carry `audience` (public | staff | owner) and customer
retrieval filters on it. Older chunks carry none, so — until this runs and
KB_CUSTOMER_AUDIENCE_STRICT is switched on — a customer's search still reaches every untagged
supplier invoice, bank statement and wage roster. docs/kb-retrieval-audit.md.

    python tools/kb_audience_backfill.py --tenant off-the-hook              # preview (default)
    python tools/kb_audience_backfill.py --tenant off-the-hook --apply      # write + read back
    python tools/kb_audience_backfill.py --all                              # preview every tenant
    python tools/kb_audience_backfill.py --tenant off-the-hook --rollback   # undo this tool's writes

Classification is per DOCUMENT (all its chunks get the same audience), first rule that matches:
  1. already tagged → left alone (idempotent). A document only partly tagged gets its untagged
     chunks set to the tag the rest already carry. --reclassify redoes tagged documents too,
     except where the owner chose the audience on the dashboard (audience_by=owner).
  2. Vula's shared collections (training, sector, network) → public
  3. source_type: website / starter / learned_answer_approved / promoted → public;
     conversation / learned → staff
  4. the filed document's category (vula_filed_documents, joined on doc_id): bank → owner;
     invoices, quotes, statements, payments, contracts, payroll, correspondence… → staff;
     menu / price list, booking policy, brochure, specification → public BUT flagged for review
  5. filename: bank/statement/payslip/payroll/wages/salary → owner; invoice/receipt/quote/
     supplier/roster/contract/… → staff; recipe-*, owner-taught, FAQ, menu → public
  6. anything else → staff (the conservative default: an owner can make it public from the
     dashboard's Documents page)

Preview prints counts only plus the filenames that need an owner's look. --apply writes with
Qdrant's set-payload (vectors untouched), reads every point back to confirm, and journals each
document's previous value to DATA_DIR/kb_audience_backfill/<collection>.jsonl, so a rerun skips
what is done (resumable) and --rollback restores exactly what was there.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PUBLIC_SOURCES = {"website", "starter", "learned_answer_approved", "promoted"}
STAFF_SOURCES = {"conversation", "learned"}

# vula_filed_documents.category (vula/api/whatsapp.py::_DOC_CATEGORIES) → (audience, needs review)
_CATEGORY_RULES: List[Tuple[re.Pattern, str, bool]] = [
    (re.compile(r"bank|settlement", re.I), "owner", False),
    (re.compile(r"payslip|payroll|salar|wage|insurance|legal|property", re.I), "owner", False),
    (re.compile(r"invoice|quote|estimate|receipt|statement|proof of payment|credit note|"
                r"delivery note|purchase order|supplier|contract|agreement|boq|bill of quantities|"
                r"fee proposal|tender|minutes|programme|report|correspondence|business card|"
                r"site / building|drawing|email attachment|general document|other file|"
                r"health & safety", re.I), "staff", False),
    (re.compile(r"menu|price list|booking policy|brochure|product info|specification", re.I),
     "public", True),
]

_FILENAME_RULES: List[Tuple[re.Pattern, str, bool, str]] = [
    (re.compile(r"bank|statement|payslip|payroll|wages?\b|salar(y|ies)|\bpaye\b|\buif\b|irp5", re.I),
     "owner", False, "money/HR filename"),
    (re.compile(r"invoice|\binv[-_ ]?\d|receipt|quot(e|ation)|supplier|roster|contract|agreement|"
                r"purchase order|\bpo[-_ ]?\d|credit note|delivery note|remittance|proof of payment|"
                r"\bpop\b|budget|costing|\bvat\b|\bsars\b|tax|pricing|cost price|trade price", re.I),
     "staff", False, "business-record filename"),
    (re.compile(r"^recipe-|^owner-taught|\bfaq\b|frequently asked|\bmenu\b", re.I),
     "public", False, "customer-content filename"),
    (re.compile(r"^(website|shopify|woocommerce) — ", re.I), "public", False, "imported website"),
]


def classify(payload: Dict[str, Any], category: Optional[str] = None,
             shared: bool = False) -> Tuple[str, str, bool]:
    """(audience, reason, needs_review) for one document, from one of its chunk payloads."""
    if shared:
        return "public", "shared Vula collection", False
    st = str(payload.get("source_type") or "")
    if st in PUBLIC_SOURCES:
        return "public", f"source_type={st}", False
    if st in STAFF_SOURCES:
        return "staff", f"source_type={st}", False
    if category:
        for rx, aud, review in _CATEGORY_RULES:
            if rx.search(category):
                return aud, f"filed as {category}", review
    fname = str(payload.get("filename") or "")
    for rx, aud, review, why in _FILENAME_RULES:
        if rx.search(fname):
            return aud, why, review
    doc_id = str(payload.get("doc_id") or "")
    if doc_id.startswith(("invoice_item_", "profile_", "code_")):
        return "staff", f"{doc_id.split('_')[0]} record", False
    return "staff", "default (unclassified)", False


# ── Qdrant / Supabase I/O ─────────────────────────────────────────────────────

def _shared_tenant_ids() -> set:
    ids = set()
    for mod, attr in (("vula.training.content", "TRAINING_TENANT_ID"),
                      ("vula.training.business_content", "BUSINESS_TRAINING_TENANT_ID"),
                      ("vula.training.network", "NETWORK_TENANT_ID")):
        try:
            ids.add(getattr(__import__(mod, fromlist=[attr]), attr))
        except Exception:
            pass
    return ids


def _categories(tenant_id: str, doc_ids: List[str]) -> Dict[str, str]:
    """{kb doc_id: filed category}. Fail-open: no Supabase → filename rules only."""
    out: Dict[str, str] = {}
    try:
        from vula.commerce import service
        for i in range(0, len(doc_ids), 200):
            rows = (service._client().table("vula_filed_documents").select("doc_id,category")
                    .eq("tenant_id", tenant_id).in_("doc_id", doc_ids[i:i + 200]).execute().data or [])
            out.update({str(r["doc_id"]): r.get("category") or "" for r in rows if r.get("doc_id")})
    except Exception as exc:  # noqa: BLE001
        print(f"  (filed categories unavailable — filename rules only: {type(exc).__name__})")
    return out


async def _documents(client, base: str, name: str) -> Dict[str, Dict[str, Any]]:
    """{doc_id: {"points": [...], "payload": first payload, "audiences": set}} — no chunk text kept."""
    docs: Dict[str, Dict[str, Any]] = {}
    offset = None
    while True:
        body: Dict[str, Any] = {"limit": 256, "with_vector": False,
                                "with_payload": ["doc_id", "filename", "source_type", "audience",
                                                 "audience_by", "category", "chunk_id"]}
        if offset is not None:
            body["offset"] = offset
        r = await client.post(f"{base}/collections/{name}/points/scroll", json=body)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        res = r.json().get("result", {})
        for p in res.get("points", []):
            pl = p.get("payload") or {}
            key = str(pl.get("doc_id") or pl.get("filename") or p["id"])
            d = docs.setdefault(key, {"points": [], "payload": pl, "tags": {}, "owner_set": False})
            d["points"].append(p["id"])
            d["tags"][p["id"]] = pl.get("audience")
            d["owner_set"] = d["owner_set"] or pl.get("audience_by") == "owner"
        offset = res.get("next_page_offset")
        if offset is None:
            return docs


def _journal_path(name: str) -> Path:
    from config import settings
    d = Path(settings.data_dir) / "kb_audience_backfill"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.jsonl"


def _journal(name: str) -> Dict[str, Dict[str, Any]]:
    p = _journal_path(name)
    out: Dict[str, Dict[str, Any]] = {}
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                out[e["doc"]] = e
    return out


async def _set(client, base: str, name: str, points: List[Any], audience: Optional[str]) -> None:
    if audience is None:
        r = await client.post(f"{base}/collections/{name}/points/payload/delete",
                              params={"wait": "true"}, json={"keys": ["audience"], "points": points})
    else:
        r = await client.post(f"{base}/collections/{name}/points/payload",
                              params={"wait": "true"}, json={"payload": {"audience": audience}, "points": points})
    r.raise_for_status()


async def _read_back(client, base: str, name: str, points: List[Any]) -> List[Any]:
    """The audience actually stored on each point (verified, not reported)."""
    got: List[Any] = []
    for i in range(0, len(points), 256):
        r = await client.post(f"{base}/collections/{name}/points",
                              json={"ids": points[i:i + 256], "with_payload": ["audience"]})
        r.raise_for_status()
        got += [(p.get("payload") or {}).get("audience") for p in r.json().get("result", [])]
    return got


async def run(tenant_id: str, apply: bool = False, rollback: bool = False,
              reclassify: bool = False, client=None) -> Dict[str, Any]:
    import httpx
    from vula.ingestion.pipeline import QdrantStore
    store = QdrantStore()
    name = store._collection_name(tenant_id)
    from vula.ingestion.pipeline import is_shared_collection
    shared = is_shared_collection(tenant_id)
    own = client is None
    client = client or httpx.AsyncClient(timeout=60.0, headers=store._headers())
    rep: Dict[str, Any] = {"tenant": tenant_id, "collection": name, "mode":
                           "rollback" if rollback else ("apply" if apply else "preview"),
                           "documents": 0, "points": 0, "by_audience": {}, "by_reason": {},
                           "already_tagged": 0, "review": [], "written": 0, "verified": 0,
                           "mismatched": 0, "skipped_done": 0}
    try:
        journal = _journal(name)
        if rollback:
            for doc, e in journal.items():
                if e.get("status") not in ("applied", "unverified"):
                    continue
                for before, pts in e["groups"]:
                    await _set(client, store.base, name, pts, before)
                    rep["written"] += len(pts)
                with _journal_path(name).open("a") as f:
                    f.write(json.dumps({**e, "status": "rolled_back"}) + "\n")
            return rep
        docs = await _documents(client, store.base, name)
        cats = {} if shared else _categories(tenant_id, [k for k in docs if not k.isdigit()])
        ensured = False
        for key, d in sorted(docs.items()):
            rep["documents"] += 1
            rep["points"] += len(d["points"])
            existing = {a for a in d["tags"].values() if a}
            untagged = [pid for pid, a in d["tags"].items() if not a]
            if d["owner_set"] or (not untagged and not reclassify):
                rep["already_tagged"] += 1
                continue
            if journal.get(key, {}).get("status") == "applied" and not reclassify and not untagged:
                rep["skipped_done"] += 1
                continue
            if untagged and len(existing) == 1 and not reclassify:
                # Part of the document was re-ingested after C5 and carries a tag already.
                aud, why, review, targets = next(iter(existing)), "matches the rest of the document", False, untagged
            else:
                aud, why, review = classify(d["payload"], cats.get(key), shared)
                targets = list(d["points"]) if reclassify else untagged
            rep["by_audience"][aud] = rep["by_audience"].get(aud, 0) + 1
            rep["by_reason"][why] = rep["by_reason"].get(why, 0) + 1
            if review:
                rep["review"].append({"filename": d["payload"].get("filename"), "audience": aud, "why": why})
            if not apply:
                continue
            if not ensured:   # the keyword index the customer filter uses
                await client.put(f"{store.base}/collections/{name}/index",
                                 json={"field_name": "audience", "field_schema": "keyword"})
                ensured = True
            # Journal what each point held BEFORE, so --rollback restores exactly that.
            groups: Dict[Any, List[Any]] = {}
            for pid in targets:
                groups.setdefault(d["tags"].get(pid), []).append(pid)
            await _set(client, store.base, name, targets, aud)
            rep["written"] += len(targets)
            stored = await _read_back(client, store.base, name, targets)
            ok = sum(1 for a in stored if a == aud)
            rep["verified"] += ok
            rep["mismatched"] += len(targets) - ok
            with _journal_path(name).open("a") as f:
                f.write(json.dumps({"doc": key, "groups": [[b, pts] for b, pts in groups.items()],
                                    "after": aud, "reason": why,
                                    "status": "applied" if ok == len(targets) else "unverified"},
                                   default=str) + "\n")
        return rep
    finally:
        if own:
            await client.aclose()


def _print(rep: Dict[str, Any]) -> None:
    print(f"{rep['collection']} [{rep['mode']}]: {rep['documents']} documents / {rep['points']} chunks; "
          f"{rep['already_tagged']} already tagged, {rep['skipped_done']} done in an earlier run")
    if rep["by_audience"]:
        print(f"  would tag / tagged: {rep['by_audience']}")
        for why, n in sorted(rep["by_reason"].items(), key=lambda x: -x[1]):
            print(f"    {n:5}  {why}")
    if rep["review"]:
        print(f"  {len(rep['review'])} made PUBLIC by category — check these are really for customers:")
        for r in rep["review"]:
            print(f"    - {r['filename']}  ({r['why']})")
    if rep["mode"] != "preview":
        print(f"  points written {rep['written']}, read back OK {rep['verified']}, mismatched {rep['mismatched']}")


async def _main(a) -> int:
    import httpx
    from vula.ingestion.pipeline import QdrantStore
    tenants = [a.tenant] if a.tenant else []
    if a.all:
        store = QdrantStore()
        async with httpx.AsyncClient(timeout=30.0, headers=store._headers()) as c:
            r = await c.get(f"{store.base}/collections")
            r.raise_for_status()
            # Collection names lost the tenant id's hyphens; map back through the tenant list.
            names = {x["name"] for x in r.json()["result"]["collections"]}
        from vula.api.tenants import _client as _tenants_db
        rows = _tenants_db().table("vula_tenant_config").select("tenant_id").execute().data or []
        known = {store._collection_name(t["tenant_id"]): t["tenant_id"] for t in rows if t.get("tenant_id")}
        known.update({store._collection_name(t): t for t in _shared_tenant_ids()})
        known.update({n: "sector_" + n[len("vula_sector_"):] for n in names if n.startswith("vula_sector_")})
        tenants = [known[n] for n in sorted(names) if n in known]
        unknown = sorted(n for n in names if n.startswith("vula_") and n not in known)
        if unknown:
            print(f"(not a known tenant, skipped: {', '.join(unknown)})")
    bad = 0
    for t in tenants:
        rep = await run(t, apply=a.apply, rollback=a.rollback, reclassify=a.reclassify)
        _print(rep)
        bad += rep["mismatched"]
    return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tenant")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--reclassify", action="store_true",
                    help="redo documents already tagged (an owner's dashboard choice included)")
    sys.exit(asyncio.run(_main(ap.parse_args())))
