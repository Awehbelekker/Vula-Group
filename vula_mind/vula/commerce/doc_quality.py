"""
vula/commerce/doc_quality.py — documents that are filed right, read fully, and easy to find.

2026-10-02 (Ian: "look at how Vula is filing, analysing, reading and pulling the correct data
from documents, so it's easy for Vula and the tenant to look for documents"). Production:

    digg-demo     759 documents — 214 in a catch-all category, 66/223 invoices with no supplier,
                  63 with no total, 347 waiting on "which project?", 192 filed under names that
                  aren't on the project register ("ATLANTIS FOODS" 110, ClickUp list names …)
    off-the-hook  273 — 101 catch-all, 35/131 invoices with no supplier and no total

The catch-alls were mostly understood — their summaries say "delivery note from Solid Cape",
"accounts receivable statement", "FNB payment notification" — but there was no category to put
them in. This module holds the pieces the rest of the pipeline shares:

  category_from_summary  the category a catch-all document's own summary names (no model)
  missing_details        which required details a filed document lacks, per category
  health                 one tenant's document numbers (dashboard card, owner advisor, benchmark)
  cleanup                the one-off tidy-up: preview first, apply only when asked
  search_clauses         one search across title, summary, party, number and project
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Where a document lands when nothing better was found.
CATCH_ALL = ("General Document", "Email attachment", "Other file")

# Categories added 2026-10-02 (also in vula/api/whatsapp.py::_DOC_CATEGORIES, with field shapes).
NEW_CATEGORIES = ["Delivery Note", "Account Statement", "Insurance", "Legal / Property",
                  "Brochure / Product Info", "Correspondence"]

# What a document of each kind must carry to be useful (search, supplier history, job costing,
# bank matching). Anything missing is re-read once; still missing → the owner fills it in.
REQUIRED: Dict[str, List[str]] = {
    "Invoice": ["supplier", "date", "total_cents"],
    "Quote / Estimate": ["supplier", "date", "total_cents"],
    "Proof of Payment": ["payee_name", "date", "amount_cents"],
    "Delivery Note": ["supplier", "date"],
    "Account Statement": ["supplier", "date"],
    "Settlement Statement": ["date", "net_cents"],
}

LABELS = {"supplier": "supplier", "date": "date", "total_cents": "total", "payee_name": "who was paid",
          "amount_cents": "amount", "net_cents": "amount paid out"}

# Summary wording → category. Ordered: the first match wins, most specific first. Only ever
# applied to a document in a catch-all category.
_SUMMARY_RULES = [
    (r"\bdelivery (?:note|slip)\b|\bpick(?:ing)? slip\b|\bgoods received\b|\bwaybill\b", "Delivery Note"),
    (r"\b(?:accounts? receivable|account|customer|supplier|creditors?|debtors?) statement\b|"
     r"\bstatement of account\b|\bage analysis\b", "Account Statement"),
    (r"\bsettlement (?:summary|statement|report)\b|\bmerchant (?:payout|settlement)\b", "Settlement Statement"),
    (r"\bpayment (?:notification|confirmation|advice)\b|\bproof of payment\b|\beft (?:confirmation|notification)\b|"
     r"\bnotification of payment\b", "Proof of Payment"),
    (r"\btax invoice\b|\binvoice\b(?! (?:request|address))", "Invoice"),
    (r"\bquotation\b|\bquote\b|\bestimate\b|\bpro ?forma\b", "Quote / Estimate"),
    (r"\binsurance\b|\bpolicy schedule\b|\bconfirmation of (?:insurance )?cover\b|\binsured\b", "Insurance"),
    (r"\btitle deed\b|\bdeed of (?:sale|transfer)\b|\blease (?:agreement)?\b|\bpower of attorney\b|"
     r"\baffidavit\b|\bcourt\b|\bsummons\b|\bzoning certificate\b|\bsectional title\b", "Legal / Property"),
    (r"\bbrochure\b|\bcatalog(?:ue)?\b|\bproduct (?:listing|description|sheet|catalog\w*)\b|"
     r"\badvertisement\b|\bdata ?sheet\b|\bflyer\b", "Brochure / Product Info"),
    (r"\bmeeting minutes\b|\bminutes of (?:the )?meeting\b", "Meeting Minutes"),
    (r"\bdrawing\b|\bfloor plan\b|\bsite plan\b|\belevation\b|\bsection drawing\b|\bconcept plan\b", "Drawing / Plan"),
    (r"\b(?:letter|email|correspondence|notice)\b (?:from|to|regarding|about)\b", "Correspondence"),
]
_SUMMARY_RES = [(re.compile(p, re.IGNORECASE), cat) for p, cat in _SUMMARY_RULES]


def category_from_summary(summary: Optional[str], filename: Optional[str] = "") -> Optional[str]:
    """The category the document's own summary (or file name) names, or None."""
    for text in (summary or "", filename or ""):
        for rx, cat in _SUMMARY_RES:
            if rx.search(text):
                return cat
    return None


# Money categories book into the ledger when a document is first filed — never promoted to on
# the summary's wording alone at that point (the fields weren't extracted in the money shape).
MONEY_CATEGORIES = ("Invoice", "Quote / Estimate", "Bill of Quantities (BOQ)")


def better_category(category: Optional[str], summary: Optional[str], filename: Optional[str] = "",
                    allow_money: bool = False) -> str:
    """`category`, unless it's a catch-all and the summary names something more specific."""
    cat = category or "General Document"
    if cat in CATCH_ALL:
        new = category_from_summary(summary, filename)
        if new and (allow_money or new not in MONEY_CATEGORIES):
            return new
    return cat


def _present(v: Any) -> bool:
    return v not in (None, "", 0, [], {})


def missing_details(category: Optional[str], fields: Optional[Dict[str, Any]]) -> List[str]:
    """Required details this document lacks (field keys), [] when complete or not required."""
    f = fields or {}
    return [k for k in REQUIRED.get(category or "", []) if not _present(f.get(k))]


def _client():
    from vula.commerce import service
    return service._client()


def _rows(tenant_id: str, cols: str) -> List[Dict[str, Any]]:
    out, start = [], 0
    while True:
        batch = (_client().table("vula_filed_documents").select(cols).eq("tenant_id", tenant_id)
                 .order("created_at", desc=True).range(start, start + 999).execute().data or [])
        out += batch
        if len(batch) < 1000 or start > 20000:
            return out
        start += 1000


def health(tenant_id: str, sample: int = 8) -> Dict[str, Any]:
    """One tenant's document numbers: catch-alls, missing details, waiting on a project, filed
    under names that aren't registered projects, and samples of each to act on."""
    rows = _rows(tenant_id, "id,filename,category,fields,project,status,created_at")
    try:
        from vula.commerce.service import project_key, registered_projects
        reg = registered_projects(tenant_id)
        known = {project_key(r.get("name")) for r in reg} | {
            project_key(a) for r in reg for a in (r.get("aliases") or [])}
    except Exception:
        known, project_key = set(), (lambda s: (s or "").lower())
    catch_all = [r for r in rows if (r.get("category") or "General Document") in CATCH_ALL]
    missing = [(r, missing_details(r.get("category"), r.get("fields"))) for r in rows]
    missing = [(r, m) for r, m in missing if m]
    pending = [r for r in rows if r.get("status") == "pending_project"]
    unregistered: Dict[str, int] = {}
    if known:
        for r in rows:
            p = r.get("project")
            if p and project_key(p) not in known:
                unregistered[p] = unregistered.get(p, 0) + 1
    by_cat: Dict[str, int] = {}
    for r in rows:
        c = r.get("category") or "General Document"
        by_cat[c] = by_cat.get(c, 0) + 1

    def _brief(r: Dict[str, Any], m: Optional[List[str]] = None) -> Dict[str, Any]:
        out = {"id": r["id"], "filename": r.get("filename"), "category": r.get("category"),
               "project": r.get("project")}
        if m:
            out["missing"] = [LABELS.get(k, k) for k in m]
        return out

    return {
        "total": len(rows),
        "categories": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
        "catch_all": len(catch_all),
        "missing_details": len(missing),
        "missing_by_category": {c: sum(1 for r, _ in missing if r.get("category") == c) for c in REQUIRED
                                if any(r.get("category") == c for r, _ in missing)},
        "pending_project": len(pending),
        "unregistered_projects": dict(sorted(unregistered.items(), key=lambda kv: -kv[1])),
        "samples": {"missing": [_brief(r, m) for r, m in missing[:sample]],
                    "catch_all": [_brief(r) for r in catch_all[:sample]]},
    }


def cleanup_preview(tenant_id: str, fix_named_mismatch: bool = False, limit: int = 5000) -> Dict[str, Any]:
    """The one-off tidy-up, as a list of changes to review. Nothing is written here.

      1. recategorise   a catch-all document whose own summary names its kind;
      2. rename_project a project spelt differently from the register or one of its aliases
                        (migration 190) — renamed to the registered name;
      3. file_pending   a document waiting on "which project?" that NAMES exactly one project
                        (project_resolver kind "named") — anything weaker stays for the owner;
      4. move_named     only with fix_named_mismatch: a filed document whose own text names a
                        different project than the one it's filed under (the 9 the benchmark
                        found on 1 Oct).
    """
    from vula.commerce.service import canonical_project, project_key
    from vula.integrations import project_resolver
    rows = _rows(tenant_id, "id,filename,category,summary,fields,project,status")[:limit]
    items: List[Dict[str, Any]] = []
    canon_cache: Dict[str, Optional[str]] = {}

    def _canon(p: str) -> Optional[str]:
        if p not in canon_cache:
            canon_cache[p] = canonical_project(tenant_id, p)
        return canon_cache[p]

    for r in rows:
        cat = r.get("category") or "General Document"
        if cat in CATCH_ALL:
            new = category_from_summary(r.get("summary"), r.get("filename"))
            if new and new != cat:
                items.append({"kind": "recategorise", "id": r["id"], "filename": r.get("filename"),
                              "field": "category", "old": r.get("category"), "new": new})
        p = r.get("project")
        canon = _canon(p) if p else None
        if p and canon and canon != p:
            items.append({"kind": "rename_project", "id": r["id"], "filename": r.get("filename"),
                          "field": "project", "old": p, "new": canon})
        pending = r.get("status") == "pending_project"
        if not (pending or (fix_named_mismatch and p)):
            continue
        text = " ".join(str(x) for x in (r.get("filename"), r.get("summary"),
                                         (r.get("fields") or {}).get("supplier")) if x)
        try:
            got = project_resolver.resolve(tenant_id, r.get("fields") or {}, text) or {}
        except Exception as exc:
            log.debug("cleanup resolve skipped for %s: %s", r.get("id"), exc)
            got = {}
        if got.get("kind") != "named" or not got.get("project"):
            continue
        if pending:
            items.append({"kind": "file_pending", "id": r["id"], "filename": r.get("filename"),
                          "field": "project", "old": p, "new": got["project"], "reason": got.get("reason")})
        elif project_key(got["project"]) != project_key(canon or p):
            items.append({"kind": "move_named", "id": r["id"], "filename": r.get("filename"),
                          "field": "project", "old": p, "new": got["project"], "reason": got.get("reason")})
    counts: Dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    return {"counts": counts, "items": items}


def apply_cleanup(tenant_id: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Write exactly the previewed changes. Each update only lands if the document still has the
    value the preview saw (a change made since is left alone), then the result is read back."""
    db = _client()
    written, skipped = 0, 0
    for it in items:
        upd: Dict[str, Any] = {it["field"]: it["new"]}
        if it["kind"] == "file_pending":
            upd["status"] = "filed"
        q = db.table("vula_filed_documents").update(upd).eq("tenant_id", tenant_id).eq("id", it["id"])
        q = q.is_(it["field"], "null") if it.get("old") is None else q.eq(it["field"], it["old"])
        res = q.execute()
        if res.data:
            written += 1
        else:
            skipped += 1
    after = health(tenant_id, sample=0)
    return {"written": written, "skipped": skipped,
            "after": {k: after[k] for k in ("catch_all", "pending_project", "unregistered_projects")}}


_SEARCH_FIELDS = ("supplier", "customer", "payee_name", "invoice_number", "reference", "provider")


def search_clauses(term: str) -> str:
    """A PostgREST or_() matching `term` (words in order) in the title, summary, project and the
    party / number fields — one search for the Documents screen and WhatsApp alike."""
    t = re.sub(r"[,()%*]", " ", term or "").strip()
    t = re.sub(r"\s+", "%", t)
    if not t:
        return ""
    clauses = [f"filename.ilike.%{t}%", f"summary.ilike.%{t}%", f"project.ilike.%{t}%",
               f"category.ilike.%{t}%"]
    clauses += [f"fields->>{k}.ilike.%{t}%" for k in _SEARCH_FIELDS]
    return ",".join(clauses)
