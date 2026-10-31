"""
vula/commerce/decisions.py — the owner's rulings, remembered with their reason (migration 235).

31 Oct 2026 (Ian, DIGG): "start with the decision log". Settling Belladonna's final account took a
run of rulings — Sea Point Hire is Belladonna, every Pudlo invoice was Belladonna tiles, DIGG paid
the R3,700 plumbing change, take out the extra tile labour, further painting is billed separately.
The books ended up with the right figures, but nothing kept the reasons: asked "why is the
plumbing R9,700?" Vula could show the figure and not the ruling, and next month it could repeat
mistakes the owner had already corrected.

A decision is a fact about what was settled, kept in the owner's words with the project it is
about and the rows it touched. It is read when that project comes up (block_for_prompt) and when
someone asks why a figure is what it is (search). Not a standing instruction (business_rules.py,
applied to every answer) and not a customer Q->A pair (learned answers).

Every query is scoped to the tenant. Best-effort reads: before migration 235 there are simply no
decisions, never an error in a reply.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TABLE = "vula_decisions"
MAX_DECISION_CHARS = 800
_BLOCK_PROJECT = 15        # decisions on the project a message names, newest first
_BLOCK_GENERAL = 5         # business-wide decisions (no project)
_MAX_BLOCK_CHARS = 3000
_SOURCES = ("whatsapp", "dashboard", "operator", "system")


def _client():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(name: Optional[str]) -> str:
    from vula.commerce.service import project_key
    return project_key(name or "")


def _canonical(tenant_id: str, project: Optional[str]) -> Optional[str]:
    raw = (project or "").strip()
    if not raw:
        return None
    try:
        from vula.commerce.service import canonical_project
        return canonical_project(tenant_id, raw) or raw
    except Exception:  # noqa: BLE001
        return raw


def _r(cents: Optional[int]) -> str:
    return f"R{(cents or 0) / 100:,.2f}"


def record(tenant_id: str, decision: str, *, project: Optional[str] = None,
           subject: Optional[str] = None, amount_cents: Optional[int] = None,
           refs: Optional[List[Dict[str, str]]] = None, made_by: Optional[str] = None,
           role: Optional[str] = None, phone: Optional[str] = None, source: str = "whatsapp",
           supersedes: Optional[str] = None) -> Dict[str, Any]:
    """Store one ruling and read it back. The same ruling on the same project twice is kept once.
    `supersedes` marks an earlier decision as replaced by this one. Raises ValueError for an empty
    or over-long ruling; RuntimeError when it didn't read back."""
    text = re.sub(r"\s+", " ", decision or "").strip()
    if not text:
        raise ValueError("What was decided?")
    if len(text) > MAX_DECISION_CHARS:
        raise ValueError("That's too long for one decision — keep it to what was settled.")
    proj = _canonical(tenant_id, project)
    db = _client()
    q = db.table(TABLE).select("*").eq("tenant_id", tenant_id).eq("status", "active")
    q = q.eq("project", proj) if proj else q.is_("project", "null")
    for r in q.limit(200).execute().data or []:
        if (r.get("decision") or "").strip().lower() == text.lower():
            return {**r, "already_known": True}
    row = {"tenant_id": tenant_id, "project": proj, "subject": (subject or "").strip()[:160] or None,
           "decision": text, "amount_cents": int(amount_cents) if amount_cents is not None else None,
           "refs": list(refs or []), "made_by": (made_by or "").strip() or None,
           "made_by_role": (role or "").strip() or None, "made_by_phone": (phone or "").strip() or None,
           "source": source if source in _SOURCES else "whatsapp", "status": "active",
           "created_at": _now(), "updated_at": _now()}
    res = db.table(TABLE).insert(row).execute()
    new_id = ((res.data or [{}])[0]).get("id")
    back = (db.table(TABLE).select("*").eq("tenant_id", tenant_id).eq("id", new_id)
            .limit(1).execute().data or []) if new_id else []
    if not back or back[0].get("decision") != text:
        raise RuntimeError("the decision was not saved")
    if supersedes:
        supersede(tenant_id, supersedes, by_id=new_id)
    log.info("decision recorded for %s on %s", tenant_id, proj or "the business")
    return back[0]


def supersede(tenant_id: str, decision_id: str, by_id: Optional[str] = None) -> bool:
    """A later ruling replaced this one. Read back."""
    return _set_status(tenant_id, decision_id, "superseded", by_id)


def withdraw(tenant_id: str, decision_id: str) -> bool:
    """The owner took this ruling back. Read back."""
    return _set_status(tenant_id, decision_id, "withdrawn")


def _set_status(tenant_id: str, decision_id: str, status: str, by_id: Optional[str] = None) -> bool:
    db = _client()
    now = (db.table(TABLE).select("status").eq("tenant_id", tenant_id).eq("id", decision_id)
           .limit(1).execute().data or [])
    if not now or now[0].get("status") != "active":
        return False               # unknown, or already superseded / taken back
    patch: Dict[str, Any] = {"status": status, "updated_at": _now()}
    if by_id:
        patch["superseded_by"] = by_id
    db.table(TABLE).update(patch).eq("tenant_id", tenant_id).eq("id", decision_id) \
        .eq("status", "active").execute()
    back = (db.table(TABLE).select("status").eq("tenant_id", tenant_id).eq("id", decision_id)
            .limit(1).execute().data or [])
    return bool(back) and back[0].get("status") == status


def for_project(tenant_id: str, project: Optional[str], include_inactive: bool = False,
                limit: int = 100) -> List[Dict[str, Any]]:
    """A project's decisions, newest first (None: the business-wide ones)."""
    try:
        q = _client().table(TABLE).select("*").eq("tenant_id", tenant_id)
        proj = _canonical(tenant_id, project)
        q = q.eq("project", proj) if proj else q.is_("project", "null")
        if not include_inactive:
            q = q.eq("status", "active")
        rows = q.order("created_at", desc=True).limit(limit).execute().data or []
        return sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)
    except Exception as exc:  # noqa: BLE001 — before migration 235
        log.debug("decisions lookup skipped: %s", exc)
        return []


def _all_active(tenant_id: str, limit: int = 500) -> List[Dict[str, Any]]:
    try:
        rows = (_client().table(TABLE).select("*").eq("tenant_id", tenant_id).eq("status", "active")
                .order("created_at", desc=True).limit(limit).execute().data or [])
        return sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)
    except Exception as exc:  # noqa: BLE001
        log.debug("decisions lookup skipped: %s", exc)
        return []


_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "and", "for", "why", "what", "was", "were", "is", "are", "did", "does", "how",
         "this", "that", "with", "from", "our", "who", "when", "much", "about"}
_AMOUNT = re.compile(r"\d[\d\s,]*(?:\.\d{1,2})?")


def _amounts(text: str) -> set:
    """Rand figures in a question as cents: '3700', 'R3,700', '9 700.00'."""
    out = set()
    for m in _AMOUNT.findall(text or ""):
        digits = re.sub(r"[\s,]", "", m)
        try:
            v = float(digits)
        except ValueError:
            continue
        if v >= 10:
            out.add(int(round(v * 100)))
    return out


def search(tenant_id: str, query: str, project: Optional[str] = None, limit: int = 8) -> List[Dict[str, Any]]:
    """Active decisions that match a question: a figure it names (as the decision's amount or in
    its words), else shared words. Restricted to one project when given."""
    rows = _all_active(tenant_id)
    if project:
        k = _key(_canonical(tenant_id, project))
        rows = [r for r in rows if _key(r.get("project")) == k]
    want_amounts = _amounts(query)
    words = {w for w in _WORD.findall((query or "").lower()) if len(w) > 2 and w not in _STOP}
    scored = []
    for r in rows:
        hay = f"{r.get('subject') or ''} {r.get('decision') or ''}".lower()
        score = 0
        if want_amounts:
            have = set(_amounts(hay))
            if r.get("amount_cents"):
                have.add(int(r["amount_cents"]))
            if want_amounts & have:
                score += 10
        score += len(words & set(_WORD.findall(hay)))
        if score:
            scored.append((score, r))
    scored.sort(key=lambda s: s[1].get("created_at") or "", reverse=True)   # newest first …
    scored.sort(key=lambda s: -s[0])                                         # … within a score
    return [r for _, r in scored[:limit]]


def find(tenant_id: str, match: str) -> Optional[Dict[str, Any]]:
    """The active decision best matching a fragment ('the plumbing one'), for withdrawing."""
    hits = search(tenant_id, match, limit=1)
    return hits[0] if hits else None


def last_by(tenant_id: str, phone: Optional[str]) -> Optional[Dict[str, Any]]:
    """The most recent active decision this person recorded ('undo that')."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)[-9:]
    for r in _all_active(tenant_id, limit=50):
        if re.sub(r"\D", "", r.get("made_by_phone") or "")[-9:] == digits:
            return r
    return None


def line(r: Dict[str, Any]) -> str:
    """One decision as a readable line: date, the ruling, who made it."""
    when = (r.get("created_at") or "")[:10]
    who = r.get("made_by") or r.get("made_by_role") or ""
    amt = f" ({_r(r['amount_cents'])})" if r.get("amount_cents") else ""
    subj = f"{r['subject']}{amt}: " if r.get("subject") else (amt.strip() + " " if amt else "")
    return f"{when} — {subj}{r.get('decision')}" + (f" — {who}" if who else "")


def _project_named(tenant_id: str, message: str) -> Optional[str]:
    try:
        from vula.integrations.project_context import detect_project
        p = detect_project(tenant_id, message or "")
        return p.get("name") if p else None
    except Exception:  # noqa: BLE001
        return None


def block_for_prompt(tenant_id: str, message: str) -> str:
    """The prompt fragment with the decisions for the project a message names, plus the most
    recent business-wide ones. Empty when there are none, so nothing changes for a business that
    has never recorded one."""
    project = _project_named(tenant_id, message)
    rows = (for_project(tenant_id, project, limit=_BLOCK_PROJECT) if project else []) \
        + for_project(tenant_id, None, limit=_BLOCK_GENERAL)
    if not rows:
        return ""
    out, total = [], 0
    for r in rows:
        s = "- " + (f"[{r['project']}] " if r.get("project") else "") + line(r)
        if total + len(s) > _MAX_BLOCK_CHARS:
            break
        out.append(s)
        total += len(s)
    return (
        "\n\nDECISIONS THE OWNER HAS MADE (the decision log) — these explain why figures are what "
        "they are. Follow them, never contradict them with a guess, and when asked why a figure "
        "is what it is, give the decision behind it with its date. If a new instruction changes "
        "one, record the new decision with record_decision.\n" + "\n".join(out)
    )
