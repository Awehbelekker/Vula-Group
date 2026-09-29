"""
vula/integrations/project_resolver.py — which project a document belongs to, from the evidence.

2026-09-28 (Ian): "when I submit a document, why is Vula not analysing it and realising it's for
a certain project — from its labelling, or the payment made with the project in its
description? It should see this payment was for HPC to XYZ supplier and allocate accordingly."
digg-demo had 338 documents waiting on "which project?": 22 named HPC, 10 Porterfield, 3 Sporty
in their own text and were still asked (the name-in-text match scored 0.5, under the 0.6 bar),
and the rest (a Solid Cape invoice says "Solid Cape") had clues nobody looked at.

resolve() weighs the clues, strongest first:
  1. named   — the document names one project: its name, number or a distinctive word of it
               ("HPC", "HPC001", "Porterfield", "Sporty") as a whole word          → 0.9
  2. paid    — the bank payment that settled it (amount ±1%, −3/+60 days, the only one) is
               on a project                                                         → 0.8
  3. usual   — its supplier's earlier documents and payments went ≥80% to one project
               (at least 3 of them)                                                 → 0.7
Anything weaker returns candidates for the "which project?" question instead of a guess.
Callers auto-file at ≥ 0.7 and say why, so a wrong call is visible and easy to change.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

AUTO_FILE = 0.7
_GENERIC = {"project", "projects", "site", "the", "and", "house", "phase", "centre", "center",
            "building", "office", "new", "old", "unit", "street", "road", "avenue", "ave", "tv",
            "pty", "ltd", "cc"}


def _client():
    from vula.commerce import service
    return service._client()


def _key(s: Optional[str]) -> str:
    from vula.commerce.service import project_key
    return project_key(s)


def _projects(tenant_id: str) -> List[Dict[str, Any]]:
    """Every project the business uses, with the words that identify each."""
    from vula.commerce.expenses import known_projects
    from vula.commerce.service import canonical_project
    names: Dict[str, Dict[str, Any]] = {}
    numbers: Dict[str, str] = {}
    try:
        for r in (_client().table("vula_projects").select("name,number").eq("tenant_id", tenant_id)
                  .limit(500).execute().data or []):
            if r.get("name"):
                names.setdefault(r["name"], {"name": r["name"], "ids": set()})
                if r.get("number"):
                    numbers[r["name"]] = r["number"]
    except Exception as exc:
        log.debug("project register read skipped: %s", exc)
    for n in known_projects(tenant_id):
        canon = canonical_project(tenant_id, n) or n
        names.setdefault(canon, {"name": canon, "ids": set()})["ids"].add(_key(n))
    for p in names.values():
        p["ids"].add(_key(p["name"]))
        if numbers.get(p["name"]):
            p["ids"].add(_key(numbers[p["name"]]))
    # A project's LEAD word identifies it when no other project shares it: "hpc" (HPC Bokaap /
    # HPC_Bokaap / HPC001), "porterfield", "sporty", "atlantis". Only the lead word — a trailing
    # place name ("bokaap") also appears on other jobs in that suburb (17 Jordaan Street,
    # Bo-Kaap is a different client's block of flats).
    word_owner: Dict[str, set] = {}
    try:        # the business's own name ("digg") never identifies one of its projects
        from vula.integrations.doc_filing import _own_names
        own = {w.lower() for n in _own_names(tenant_id) for w in n.split()}
    except Exception:
        own = set()
    for p in names.values():
        for ident in p["ids"]:
            lead = next((w for w in ident.split() if not w.isdigit() and w not in _GENERIC), "")
            for cand in {lead, re.sub(r"\d+$", "", lead)}:     # hpc001 → hpc
                if len(cand) >= 3 and cand not in _GENERIC and cand not in own:
                    word_owner.setdefault(cand, set()).add(p["name"])
    for p in names.values():
        p["words"] = {w for w, owners in word_owner.items() if owners == {p["name"]}}
    return list(names.values())


def _named(projects: List[Dict[str, Any]], text: str) -> List[str]:
    t = " " + _key(text) + " "
    t2 = " " + re.sub(r"(\D)(\d)", r"\1 \2", _key(text)) + " "       # "hpc001" also as "hpc 001"
    hits = []
    for p in projects:
        idents = [i for i in p["ids"] if len(i) >= 3]
        if any(f" {i} " in t for i in idents) or any(f" {w} " in t or f" {w} " in t2 for w in p["words"]):
            hits.append(p["name"])
    return hits


def _by_payment(tenant_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        total = int(fields.get("total_cents") or fields.get("amount_cents") or 0)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    try:
        when = date.fromisoformat(str(fields.get("date"))[:10])
    except (TypeError, ValueError):
        when = None
    tol = max(100, int(total * 0.01))
    q = (_client().table("commerce_bank_transactions")
         .select("id,txn_date,description,amount_cents,project,match_status")
         .eq("tenant_id", tenant_id).eq("direction", "out")
         .gte("amount_cents", total - tol).lte("amount_cents", total + tol))
    if when:
        q = q.gte("txn_date", (when - timedelta(days=3)).isoformat()).lte(
            "txn_date", (when + timedelta(days=60)).isoformat())
    try:
        rows = [r for r in (q.limit(20).execute().data or []) if r.get("match_status") != "ignored"]
    except Exception as exc:
        log.debug("payment lookup skipped: %s", exc)
        return None
    if len(rows) != 1 or not rows[0].get("project"):
        return None
    r = rows[0]
    return {"project": r["project"], "txn": r}


def _by_supplier(tenant_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from vula.commerce.party import resolve_party_name
    from vula.commerce.service import _canonical_party
    party = resolve_party_name(fields or {}, exclude=("payer",))
    if not party:
        return None
    from vula.integrations.doc_filing import _is_own, _own_names
    if _is_own(party, _own_names(tenant_id)):
        return None                    # the business's own documents say nothing about the job
    pkey = _canonical_party(party)
    first = (pkey.split() or [""])[0]
    if len(first) < 3:
        return None
    counts: Counter = Counter()
    try:
        docs = (_client().table("vula_filed_documents").select("project,fields")
                .eq("tenant_id", tenant_id).eq("status", "filed")
                .ilike("fields->>supplier", f"%{first}%").limit(500).execute().data or [])
    except Exception as exc:
        log.debug("supplier history (documents) skipped: %s", exc)
        docs = []
    for d in docs:
        sup = (d.get("fields") or {}).get("supplier")
        if d.get("project") and sup and _canonical_party(sup) == pkey:
            counts[_key(d["project"])] += 1
    n = sum(counts.values())
    if n < 3:
        return None
    top, hits = counts.most_common(1)[0]
    if hits < 0.8 * n:
        return {"ambiguous": True, "candidates": [k for k, _ in counts.most_common(3)]}
    return {"project_key": top, "hits": hits, "of": n, "supplier": party}


def resolve(tenant_id: str, fields: Optional[Dict[str, Any]], text: str = "") -> Optional[Dict[str, Any]]:
    fields = fields or {}
    projects = _projects(tenant_id)
    if not projects:
        return None
    by_key = {_key(p["name"]): p["name"] for p in projects}
    blob = " ".join([text or ""] + [str(v) for v in fields.values() if isinstance(v, (str, int, float))])

    named = _named(projects, blob)
    if len(named) == 1:
        return {"project": named[0], "confidence": 0.9, "kind": "named", "reason": "named in the document"}

    paid = _by_payment(tenant_id, fields)
    if paid:
        from vula.commerce.service import canonical_project
        t = paid["txn"]
        return {"project": canonical_project(tenant_id, paid["project"]) or paid["project"],
                "confidence": 0.8, "kind": "paid",
                "reason": f"paid by the bank payment “{t.get('description')}” on {t.get('txn_date')}, "
                          f"which is on {paid['project']}",
                "txn_id": t.get("id")}

    usual = _by_supplier(tenant_id, fields)
    if usual and not usual.get("ambiguous"):
        name = by_key.get(usual["project_key"])
        if name:
            return {"project": name, "confidence": 0.7, "kind": "usual_supplier",
                    "reason": f"{usual['supplier']}'s usual project ({usual['hits']} of {usual['of']} earlier documents)"}

    candidates = named or [by_key[k] for k in (usual or {}).get("candidates", []) if k in by_key]
    if candidates:
        return {"project": None, "ambiguous": True, "candidates": candidates, "confidence": 0.0}
    from vula.commerce.party import resolve_party_name
    from vula.integrations.doc_filing import _is_own, _own_names
    party = resolve_party_name(fields, exclude=("payer",))
    own_doc = bool(party) and _is_own(party, _own_names(tenant_id))
    # The business's own documents (its estimates, deposit invoices, council plans) are often
    # for a NEW job — "most of this month was HPC" says nothing about them.
    busy = None if own_doc else _month_project(tenant_id, fields)
    if busy:
        # Not proof — a suggestion the owner confirms (never auto-filed).
        return {"project": busy["project"], "confidence": 0.5, "kind": "month",
                "reason": f"{busy['share']}% of {busy['month']}'s project spend was on {busy['project']}"}
    return None


_MONTH_CACHE: Dict[tuple, Optional[Dict[str, Any]]] = {}


def _month_project(tenant_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The project that took ≥ 80% of the allocated project spend in the document's month —
    Ian: "July till now would be HPC". A hint, not evidence."""
    month = str(fields.get("date") or "")[:7]
    if len(month) != 7:
        return None
    ck = (tenant_id, month)
    if ck in _MONTH_CACHE:
        return _MONTH_CACHE[ck]
    out = None
    try:
        rows = (_client().table("commerce_bank_transactions").select("project,amount_cents,match_status")
                .eq("tenant_id", tenant_id).eq("direction", "out")
                .gte("txn_date", f"{month}-01").lte("txn_date", f"{month}-31")
                .limit(2000).execute().data or [])
        spend: Counter = Counter()
        for r in rows:
            if r.get("project") and r.get("match_status") != "ignored":
                spend[r["project"]] += int(r.get("amount_cents") or 0)
        total = sum(spend.values())
        if total:
            top, cents = spend.most_common(1)[0]
            if cents >= 0.8 * total:
                out = {"project": top, "share": round(100 * cents / total), "month": month}
    except Exception as exc:
        log.debug("month project skipped: %s", exc)
    _MONTH_CACHE[ck] = out
    return out


# ── the waiting documents (backfill) ──────────────────────────────────────────

def sort_pending(tenant_id: str, apply: bool = False, limit: int = 1000,
                 include_suggested: bool = False) -> Dict[str, Any]:
    """Run resolve() over the documents waiting on "which project?". apply=False only reports
    what would happen; apply=True files the confident ones (≥ AUTO_FILE) and carries the project
    to the committed bill/quote and its prices. Nothing learned from these — only confirmations
    teach the filing rules."""
    from vula.commerce.ledger import _all_pages

    def make():
        return (_client().table("vula_filed_documents")
                .select("id,filename,category,summary,fields,commerce_invoice_id")
                .eq("tenant_id", tenant_id).eq("status", "pending_project").order("created_at"))
    rows = _all_pages(make)[:limit]
    _MONTH_CACHE.clear()
    plan, by_project, by_reason = [], Counter(), Counter()
    suggested: Counter = Counter()
    left = 0
    for r in rows:
        res = resolve(tenant_id, r.get("fields") or {}, f"{r.get('filename') or ''} {r.get('summary') or ''}")
        if res and res.get("kind") == "month":
            suggested[res["project"]] += 1
        if res and res.get("project") and (res["confidence"] >= AUTO_FILE
                                           or (include_suggested and res.get("kind") == "month")):
            plan.append({"id": r["id"], "filename": r.get("filename"), "project": res["project"],
                         "reason": res["reason"], "commerce_invoice_id": r.get("commerce_invoice_id")})
            by_project[res["project"]] += 1
            by_reason[res.get("kind")] += 1
        else:
            left += 1
    filed = 0
    if apply:
        db = _client()
        for p in plan:
            try:
                (db.table("vula_filed_documents").update({"project": p["project"], "status": "filed"})
                 .eq("tenant_id", tenant_id).eq("id", p["id"]).execute())
                if p.get("commerce_invoice_id"):
                    (db.table("commerce_invoices").update({"project": p["project"]})
                     .eq("tenant_id", tenant_id).eq("id", p["commerce_invoice_id"]).execute())
                from vula.commerce.price_book import set_project
                set_project(tenant_id, p["id"], p["project"])
                filed += 1
            except Exception as exc:
                log.warning("sort_pending: %s failed: %s", p["id"], exc)
    return {"waiting": len(rows), "would_file": len(plan), "filed": filed, "still_ask": left,
            "suggested_by_month": dict(suggested),
            "by_project": dict(by_project), "by_reason": dict(by_reason),
            "sample": plan[:40]}
