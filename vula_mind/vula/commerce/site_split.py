"""vula/commerce/site_split.py — one supplier invoice that covers several sites, split per project.

10 Nov 2026 (DIGG): skip invoices list each skip with the site it went to, so one invoice can be for
several jobs — invoice 1091 (R4,150) was R1,400 for "07 Belladonna Drive" and R2,750 for "8 Wessex,
Paarden Island", but the whole payment sat on Belladonna. The owner split it by hand; this does the
reading for them:

  match_project(text, projects)        the one project a line's words name (name or alias), else None
  propose(fields, projects, filed)     {"parts": [{project, amount_cents, lines}], "unmatched", ...} when
                                       the invoice's lines name two or more projects, else None
  at_filing(tenant, fields, project)   fields with the proposal recorded (status "proposed") and kept off
                                       client links (it shows other clients' addresses), plus the issue
                                       to tell the sender — never raises
  payments(tenant, doc)                the bank payments that could be this invoice's
  apply(tenant, doc_id, txn_id, by)    split that bank payment by the proposal (vula/commerce/bank_splits.py),
                                       read back, and mark the proposal applied

Nothing is split until the owner confirms. Money in integer cents: when the lines exclude VAT and the
invoice total includes it, each project's share is scaled to the total, the rounding going to the
largest share, so the parts always add up to the invoice.
"""
from __future__ import annotations

import difflib
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Words that say where, not which job — never enough on their own to name a project.
_GENERIC = frozenset("""
the and for with from road rd street str drive dr avenue ave lane way close crescent place park site
house flat unit block office phase project projects job new old main north south east west cape town
foods food centre center store shop building builders services group pty ltd cc company skip skips
rental hire with removal glass delivery collection invoice island eiland bay
""".split())
SPLIT_CATEGORIES = frozenset({"Invoice", "Receipt", "Proof of Payment", "Delivery Note"})


def _client():
    from vula.commerce.service import _client as c
    return c()


def _r(cents: int) -> str:
    return f"R{cents / 100:,.2f}"


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())]


def _marks(project: Dict[str, Any]) -> List[str]:
    """The distinctive words of a project's name and aliases ("HPC", "Belladonna", "Paarden")."""
    out: List[str] = []
    for name in [project.get("name") or ""] + list(project.get("aliases") or []):
        for w in _words(name):
            if len(w) >= 3 and not w.isdigit() and w not in _GENERIC and w not in out:
                out.append(w)
    return out


def _hit(mark: str, words: List[str], squashed: str) -> bool:
    if mark in words or (len(mark) >= 6 and mark in squashed):
        return True
    # a long word misspelt on a handwritten or scanned invoice ("Belladona"), or written in two ("Bella dona")
    pairs = words + [a + b for a, b in zip(words, words[1:])]
    return len(mark) >= 7 and any(len(w) >= 6 and difflib.SequenceMatcher(None, mark, w).ratio() >= 0.88 for w in pairs)


def match_project(text: str, projects: List[Dict[str, Any]]) -> Optional[str]:
    """The one project the text names, by the distinctive words of its name or aliases. None when no
    project is named, or when two are named equally (never a guess)."""
    words = _words(text)
    squashed = "".join(words)
    scored: List[Tuple[int, str]] = []
    for p in projects:
        n = sum(1 for m in _marks(p) if _hit(m, words, squashed))
        if n:
            scored.append((n, p["name"]))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def _line_cents(li: Dict[str, Any]) -> Optional[int]:
    for k in ("total_cents", "amount_cents", "line_total_cents"):
        v = li.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
    q, u = li.get("quantity"), li.get("unit_price_cents")
    if isinstance(u, (int, float)) and not isinstance(u, bool):
        try:
            return int(round(float(q if q not in (None, "") else 1) * u))
        except (TypeError, ValueError):
            return None
    return None


def _line_text(li: Dict[str, Any]) -> str:
    return " ".join(str(li.get(k) or "") for k in ("description", "site", "area", "location", "address", "reference"))


def propose(fields: Dict[str, Any], projects: List[Dict[str, Any]],
            filed_project: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The split an invoice's own lines call for, or None when they don't name two or more projects.
    Lines that name no project are listed as unmatched and stay with the project the invoice was filed
    to (or the largest share when it was filed to none)."""
    lines = [li for li in (fields or {}).get("line_items") or [] if isinstance(li, dict)]
    shares: Dict[str, Dict[str, Any]] = {}
    unmatched: List[Dict[str, Any]] = []
    for li in lines:
        cents = _line_cents(li)
        if not cents or cents <= 0:
            continue
        text = _line_text(li)
        project = match_project(text, projects)
        if project is None:
            unmatched.append({"description": text.strip()[:160], "amount_cents": cents})
            continue
        s = shares.setdefault(project, {"project": project, "amount_cents": 0, "lines": []})
        s["amount_cents"] += cents
        s["lines"].append(text.strip()[:160])
    if len(shares) < 2:
        return None
    # Only a real invoice for several sites: it has a total its lines add up to (VAT allowed for), and
    # most of its value is on lines that name a job — never a bank statement filed as an "invoice",
    # whose lines are transactions that happen to mention projects.
    named = sum(s["amount_cents"] for s in shares.values())
    every = named + sum(u["amount_cents"] for u in unmatched)
    total = (fields or {}).get("total_cents")
    if not isinstance(total, int) or total <= 0 or not (every <= total <= every * 1.16 + 100) or named * 2 < every:
        return None
    home =filed_project if filed_project in shares else max(shares.values(), key=lambda s: s["amount_cents"])["project"]
    for u in unmatched:
        shares[home]["amount_cents"] += u["amount_cents"]
    lines_total = sum(s["amount_cents"] for s in shares.values())
    scaled = False
    if total != lines_total:
        # e.g. lines before VAT, total after: each share in proportion, the rounding to the largest
        parts = sorted(shares.values(), key=lambda s: -s["amount_cents"])
        for s in parts:
            s["amount_cents"] = s["amount_cents"] * total // lines_total
        parts[0]["amount_cents"] += total - sum(s["amount_cents"] for s in parts)
        scaled = True
    parts = sorted(shares.values(), key=lambda s: (s["project"] != home, -s["amount_cents"]))
    return {"parts": parts, "unmatched": unmatched, "home": home, "scaled": scaled,
            "total_cents": sum(s["amount_cents"] for s in parts)}


def describe(proposal: Dict[str, Any]) -> str:
    return "; ".join(f"{p['project']} {_r(p['amount_cents'])}" for p in proposal["parts"])


def _projects(tenant_id: str) -> List[Dict[str, Any]]:
    """The tenant's projects with their aliases — the register first, then names seen on its books."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        for r in (_client().table("vula_projects").select("name,aliases").eq("tenant_id", tenant_id)
                  .limit(500).execute().data or []):
            if (r.get("name") or "").strip():
                out[r["name"].strip()] = {"name": r["name"].strip(), "aliases": r.get("aliases") or []}
    except Exception as exc:  # noqa: BLE001
        log.debug("project register read skipped: %s", exc)
    try:
        from vula.commerce import expenses
        for n in expenses.known_projects(tenant_id):
            out.setdefault(n, {"name": n, "aliases": []})
    except Exception as exc:  # noqa: BLE001
        log.debug("known projects read skipped: %s", exc)
    return list(out.values())


def at_filing(tenant_id: str, category: str, fields: Optional[Dict[str, Any]],
              project: Optional[str]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(fields, issues) for a document being filed: an invoice whose lines name two or more projects
    gets its proposed split recorded, is kept off client links (it carries other clients' sites), and
    the sender is told. Never raises."""
    fields = dict(fields or {})
    try:
        if category not in SPLIT_CATEGORIES or not fields.get("line_items"):
            return fields, []
        prop = propose(fields, _projects(tenant_id), project)
        if not prop:
            return fields, []
        fields["_site_split"] = {"status": "proposed", **prop}
        fields["_hidden_from_client"] = True
        return fields, [{"code": "multi_site",
                         "message": (f"This invoice covers {len(prop['parts'])} jobs: {describe(prop)}. I've kept it "
                                     "off client links (it shows each site). Confirm the split under Home › Back "
                                     "house › To check, and I'll split its payment across the jobs.")}]
    except Exception as exc:  # noqa: BLE001 — never stop a document being filed
        log.warning("site split check skipped: %s", exc)
        return fields, []


def _doc(tenant_id: str, doc_id: str) -> Optional[Dict[str, Any]]:
    rows = (_client().table("vula_filed_documents").select("id,project,category,filename,fields,created_at")
            .eq("tenant_id", tenant_id).eq("id", doc_id).limit(1).execute().data or [])
    return rows[0] if rows else None


def payments(tenant_id: str, doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Money-out bank lines for exactly the invoice's total, from 10 days before its date to 90 after
    (skips and hire are often paid on collection, weeks later)."""
    f = doc.get("fields") or {}
    total = (f.get("_site_split") or {}).get("total_cents") or f.get("total_cents")
    if not isinstance(total, int) or total <= 0:
        return []
    from vula.commerce import bank_splits
    rows = bank_splits.find_lines(tenant_id, total)
    when = str(f.get("date") or f.get("issue_date") or doc.get("created_at") or "")[:10]
    try:
        d = date.fromisoformat(when)
        rows = [r for r in rows if d - timedelta(days=10) <= date.fromisoformat(str(r["txn_date"])[:10]) <= d + timedelta(days=90)]
    except ValueError:
        pass
    return rows


def pending(tenant_id: str, docs: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Invoices with a proposed split not yet applied — the owner's to-check list."""
    from vula.commerce import data_checks
    docs = docs if docs is not None else data_checks._documents(tenant_id)
    out = []
    for d in docs:
        s = (d.get("fields") or {}).get("_site_split") or {}
        if s.get("status") == "proposed" and s.get("parts"):
            out.append({"id": d["id"], "project": d.get("project"), "filename": d.get("filename"),
                        "detail": f"{_r(s['total_cents'])} across {len(s['parts'])} jobs: {describe(s)}",
                        "parts": [{"project": p["project"], "amount_cents": p["amount_cents"]} for p in s["parts"]],
                        "action": "site_split"})
    return out


def apply(tenant_id: str, doc_id: str, txn_id: Optional[str] = None, by: Optional[str] = None) -> Dict[str, Any]:
    """Split the invoice's bank payment by its proposal. `txn_id` picks the payment when more than one
    could be it. LookupError when there's no proposal or no payment to split yet; ValueError when the
    payment can't be chosen without the owner. Read back."""
    from vula.commerce import bank_splits
    doc = _doc(tenant_id, doc_id)
    s = ((doc or {}).get("fields") or {}).get("_site_split") or {}
    if not doc or not s.get("parts"):
        raise LookupError("There's no split to apply on that document.")
    found = payments(tenant_id, doc)
    if txn_id:
        line = next((r for r in found if str(r["id"]) == str(txn_id)), None)
        if not line:
            raise LookupError("That bank payment doesn't match this invoice's total and date.")
    elif len(found) == 1:
        line = found[0]
    elif not found:
        raise LookupError(f"I can't find the {_r(s['total_cents'])} payment on your bank statements yet. "
                          "Load the statement that has it, then confirm again.")
    else:
        raise ValueError("More than one bank payment could be this invoice's — pick one.")
    db = _client()
    parts = s["parts"]
    own = line.get("project")
    if not own or own not in {p["project"] for p in parts}:
        own = s.get("home") or parts[0]["project"]
        db.table("commerce_bank_transactions").update({"project": own}).eq("tenant_id", tenant_id).eq("id", line["id"]).execute()
        line = {**line, "project": own}
    note = f"{doc.get('filename') or 'Invoice'}: {describe(s)}"
    out = bank_splits.split(tenant_id, line["id"], [
        {"project": p["project"], "amount_cents": p["amount_cents"], "note": note}
        for p in parts if p["project"] != own], by=by, source="site_split")
    fields = dict(doc.get("fields") or {})
    fields["_site_split"] = {**s, "status": "applied", "txn_id": str(line["id"]), "by": by,
                             "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    db.table("vula_filed_documents").update({"fields": fields}).eq("tenant_id", tenant_id).eq("id", doc_id).execute()
    back = _doc(tenant_id, doc_id)
    if ((back or {}).get("fields") or {}).get("_site_split", {}).get("status") != "applied":
        raise RuntimeError("the split was made but the invoice didn't record it — please check")
    return {"payment": {k: line.get(k) for k in ("id", "txn_date", "description", "amount_cents", "project")},
            "parts": [{"project": own, "amount_cents": out["remainder_cents"]}]
            + [{"project": p["project"], "amount_cents": int(p["amount_cents"])} for p in out["parts"]]}
