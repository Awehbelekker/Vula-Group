"""
vula/commerce/expenses.py — expense CLAIMS: who paid for the business, what for, where it
belongs (category/account/project), and whether they must be reimbursed.

Two entry points: a receipt snapped on WhatsApp (owner or team) and a manual entry from the
dashboard. Both land in one place — `commerce_expenses` — categorised against the Books chart
(learned rules → AI → default) with input VAT backed out, optionally allocated to a project, and
tracked through a claim lifecycle (submitted → approved → reimbursed) when someone is out of pocket.

Builds on: accounting.categorize / vat_for / is_vat_registered (Books), doc_filing project list.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def _client():
    from vula.commerce import service
    return service._client()


def _now():
    from vula.commerce import service
    return service._now()


# ── Project matching (so a claim lands on the right site/job) ──────────────────

def known_projects(tenant_id: str) -> List[str]:
    """Distinct project names Vula already knows for this tenant (field ops + prior expenses)."""
    names: set[str] = set()
    try:
        from vula.integrations import doc_filing
        for p in (doc_filing._field_projects(tenant_id) or []):
            if p:
                names.add(str(p))
    except Exception:
        pass
    db = _client()
    # Canonical project register (HPC Bokaap etc. live here).
    try:
        for r in (db.table("vula_projects").select("name").eq("tenant_id", tenant_id)
                  .limit(500).execute().data or []):
            v = (r.get("name") or "").strip()
            if v:
                names.add(v)
    except Exception:
        pass
    for table, col in (("commerce_expenses", "project"), ("commerce_invoices", "project")):
        try:
            for r in (db.table(table).select(col).eq("tenant_id", tenant_id)
                      .not_.is_(col, "null").limit(500).execute().data or []):
                v = (r.get(col) or "").strip()
                if v:
                    names.add(v)
        except Exception:
            pass
    return sorted(names)


def known_sections(tenant_id: str, project: Optional[str] = None) -> List[str]:
    """Distinct BoQ trade-section names Vula already knows (migration 129) — from a project's
    persisted BoQ breakdown (vula_project_boq.sections) plus any section already used on a real
    expense. `project` optionally scopes to one project's BoQ; omitted, unions across all of
    them (still useful as an "easy pick" list even before a specific project is chosen)."""
    names: set[str] = set()
    db = _client()
    try:
        q = db.table("vula_project_boq").select("project,sections").eq("tenant_id", tenant_id)
        if project:
            q = q.eq("project", project)
        for r in (q.limit(500).execute().data or []):
            for s in (r.get("sections") or []):
                v = (s.get("section") or "").strip() if isinstance(s, dict) else ""
                if v:
                    names.add(v)
    except Exception:
        pass
    try:
        for r in (db.table("commerce_expenses").select("section").eq("tenant_id", tenant_id)
                  .not_.is_("section", "null").limit(500).execute().data or []):
            v = (r.get("section") or "").strip()
            if v:
                names.add(v)
    except Exception:
        pass
    return sorted(names)


def match_project(tenant_id: str, text: str, projects: Optional[List[str]] = None) -> Optional[str]:
    """Loose-match a project from free text (e.g. 'for the bokaap site') to a known project name.
    Returns the canonical project name, or None if nothing matches confidently."""
    text = (text or "").strip().lower()
    if not text:
        return None
    projects = projects if projects is not None else known_projects(tenant_id)
    # exact / substring first
    for p in projects:
        if p.lower() in text or text in p.lower():
            return p
    # token overlap (e.g. "HPC Bokaap" ~ "hpc_bokaap" ~ "bokaap hpc")
    def toks(s):
        return {t for t in re.findall(r"[a-z0-9]{2,}", s.lower()) if t not in _PROJ_STOP}
    want = toks(text)
    best, best_score = None, 0
    for p in projects:
        score = len(want & toks(p))
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= 1 else None


_PROJ_STOP = {"the", "for", "site", "project", "job", "this", "that", "on", "at", "to"}


# ── Purpose category (why was this spent — for the monthly Recon sheet) ────────
# Real vocabulary from Ian's own already-in-use claim sheet (migration 140/141), not a generic
# SARS category list: PETROL / CLIENTS (client refreshments/entertainment) / ACCOMMODATION,
# plus OTHER as the catch-all.

PURPOSE_CATEGORIES = ("petrol", "clients", "accommodation", "other")

_PETROL_VENDOR_RE = re.compile(
    r"\b(engen|shell|sasol|total|caltex|bp|garage|service station|fuel|petrol|diesel)\b", re.I)
_ACCOMMODATION_VENDOR_RE = re.compile(
    r"\b(hotel|lodge|guest ?house|b&b|bnb|inn|city lodge|protea|garden court|road lodge|"
    r"holiday inn|premier hotel|airbnb|accommodation)\b", re.I)

_PURPOSE_REPLY_KEYWORDS = {
    "petrol": ("fuel", "petrol", "diesel", "gas", "garage"),
    "clients": ("client", "customer", "meeting", "lunch", "dinner", "coffee", "refreshment", "entertain"),
    "accommodation": ("hotel", "accommodation", "stay", "lodge", "night", "sleepover"),
}


def classify_purpose_category_deterministic(vendor: str) -> Optional[str]:
    """Cheap, no-LLM first pass — a confident vendor-name match against a known fuel-station or
    accommodation chain/keyword. Same 'check first, escalate only if needed' discipline as
    extraction_quality.py's reconciliation-then-escalate pattern."""
    v = (vendor or "").strip()
    if not v:
        return None
    if _PETROL_VENDOR_RE.search(v):
        return "petrol"
    if _ACCOMMODATION_VENDOR_RE.search(v):
        return "accommodation"
    return None


async def classify_purpose_category(tenant_id: str, vendor: str, amount_cents: int,
                                    notes: str = "") -> str:
    """Returns one of PURPOSE_CATEGORIES, or 'uncertain' when neither the deterministic pass nor
    the LLM can confidently place it — the only case that should trigger a WhatsApp question.
    Never raises — any failure degrades to 'uncertain' (ask, don't guess)."""
    det = classify_purpose_category_deterministic(vendor)
    if det:
        return det

    try:
        import json
        import litellm
        from core.llm_router import resolve_generation_route
        prompt = (
            "A small-business sales rep scanned a receipt. Classify what it was for, choosing "
            "ONLY ONE of: petrol (fuel/diesel), clients (a meal, coffee, or gift for a client — "
            "client entertainment/refreshments), accommodation (a hotel/lodge stay), other (does "
            "not clearly fit any of those — e.g. a car rental, vehicle repair, parking, office "
            "supplies), or uncertain (genuinely can't tell from the vendor/amount alone — e.g. a "
            "generic supermarket or shop where it could be personal, client-related, or business "
            "supplies). Prefer 'other' over 'uncertain' whenever the vendor clearly ISN'T "
            "petrol/clients/accommodation, even if you don't know exactly what it was for — only "
            "use 'uncertain' when you genuinely cannot tell.\n\n"
            f"Vendor: {vendor or 'unknown'}\nAmount: R{(amount_cents or 0) / 100:.2f}\n"
            f"Notes: {notes or 'none'}\n\n"
            "Reply with STRICT JSON only: {\"category\": \"...\"}"
        )
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route(task_type="expense_classification")
        resp = await litellm.acompletion(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=200, api_key=api_key, api_base=api_base,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        i, j = raw.find("{"), raw.rfind("}")
        if i < 0 or j <= i:
            return "uncertain"
        data = json.loads(raw[i:j + 1])
        cat = str(data.get("category") or "").strip().lower()
        return cat if cat in PURPOSE_CATEGORIES else "uncertain"
    except Exception as exc:
        log.debug("purpose category classification skipped for %s: %s", tenant_id, exc)
        return "uncertain"


def match_purpose_category(text: str) -> Optional[str]:
    """Loose-match a purpose category from a free-text reply (e.g. 'coffee with a client').
    Returns None if nothing matches — the caller then falls back to 'other', keeping the raw
    text as purpose_detail rather than silently dropping it."""
    low = (text or "").strip().lower()
    if not low:
        return None
    for cat, kws in _PURPOSE_REPLY_KEYWORDS.items():
        if any(kw in low for kw in kws):
            return cat
    return None


def set_purpose_category(tenant_id: str, expense_id: str, category: str,
                         detail: Optional[str] = None) -> dict:
    """Apply a resolved purpose category (auto-classified or from a rep's reply)."""
    patch: Dict[str, Any] = {"purpose_category": category, "updated_at": _now()}
    if detail is not None:
        patch["purpose_detail"] = detail
    res = (_client().table("commerce_expenses").update(patch)
           .eq("tenant_id", tenant_id).eq("id", expense_id).execute())
    return (res.data or [{}])[0]


def parse_odometer_reading(text: str) -> Optional[int]:
    """A rep's reply to 'what's the odometer reading at this fill-up?' — accepts '45280',
    '45,280', '45280km', '45 280 kms'. Returns None if the text isn't a plausible reading
    (so a genuinely unrelated message never gets silently misread as an odometer value)."""
    cleaned = re.sub(r"(?i)\s*kms?\.?\s*$", "", (text or "").strip())
    cleaned = cleaned.replace(",", "").replace(" ", "")
    if not cleaned.isdigit():
        return None
    km = int(cleaned)
    return km if 0 < km < 2_000_000 else None  # sanity ceiling — not a real odometer otherwise


def set_odometer(tenant_id: str, expense_id: str, km: int) -> dict:
    res = (_client().table("commerce_expenses").update(
                {"odometer_km": km, "updated_at": _now()})
           .eq("tenant_id", tenant_id).eq("id", expense_id).execute())
    return (res.data or [{}])[0]


def last_odometer_before(tenant_id: str, paid_by: str, before_date: str,
                         exclude_id: Optional[str] = None) -> Optional[int]:
    """This rep's most recent PRIOR petrol fill-up's odometer reading (any month, not just the
    reporting period) — the seed value a KM-since-last-fill-up delta needs for the first petrol
    claim of a given month."""
    try:
        q = (_client().table("commerce_expenses").select("id,odometer_km")
             .eq("tenant_id", tenant_id).eq("paid_by", paid_by).eq("purpose_category", "petrol")
             .not_.is_("odometer_km", "null").lt("date", before_date)
             .order("date", desc=True).limit(5).execute().data or [])
        for row in q:
            if row["id"] != exclude_id:
                return row["odometer_km"]
    except Exception as exc:
        log.debug("last_odometer_before lookup skipped: %s", exc)
    return None


# ── Company cards (whose money?) ───────────────────────────────────────────────

def list_cards(tenant_id: str, active_only: bool = True) -> List[dict]:
    try:
        q = _client().table("commerce_cards").select("*").eq("tenant_id", tenant_id)
        if active_only:
            q = q.eq("active", True)
        return q.execute().data or []
    except Exception as exc:
        log.debug("list_cards skipped (run migration 061?): %s", exc)
        return []


def upsert_card(tenant_id: str, last4: str, label: Optional[str] = None) -> dict:
    last4 = re.sub(r"\D", "", last4 or "")[-4:]
    if len(last4) != 4:
        raise ValueError("last4 must be 4 digits")
    res = _client().table("commerce_cards").upsert(
        {"tenant_id": tenant_id, "last4": last4, "label": label, "active": True},
        on_conflict="tenant_id,last4").execute()
    return (res.data or [{}])[0]


def delete_card(tenant_id: str, card_id: str) -> None:
    _client().table("commerce_cards").delete().eq("tenant_id", tenant_id).eq("id", card_id).execute()


def resolve_paid_with(tenant_id: str, *, card_last4: Optional[str] = None,
                      payment_method: Optional[str] = None) -> Optional[str]:
    """Whose money, from what the receipt shows: registered company card → company_card;
    a different card → personal; cash → cash; nothing readable → None (ask).
    Suffix-tolerant: Capitec prints 'Card 572' (3 digits) while slips may show 4."""
    digits = re.sub(r"\D", "", card_last4 or "")[-4:]
    if len(digits) >= 3:
        for c in list_cards(tenant_id):
            reg = (c.get("last4") or "")
            if reg.endswith(digits) or digits.endswith(reg[-3:]):
                return "company_card"
        return "personal"
    if (payment_method or "").lower() == "cash":
        return "cash"
    return None


# ── Create a claim ─────────────────────────────────────────────────────────────

async def create_claim(
    tenant_id: str,
    *,
    amount_cents: int,
    description: str,
    supplier: Optional[str] = None,
    supplier_id: Optional[str] = None,
    date: Optional[str] = None,
    vat_cents: Optional[int] = None,
    category: Optional[str] = None,
    account_code: Optional[str] = None,
    project: Optional[str] = None,
    section: Optional[str] = None,
    paid_by: Optional[str] = None,
    paid_by_name: Optional[str] = None,
    reimbursable: bool = False,
    paid_with: Optional[str] = None,      # 'company_card' | 'personal' | 'cash' | None=unknown
    card_last4: Optional[str] = None,
    channel: str = "dashboard",
    receipt_doc_id: Optional[str] = None,
    receipt_url: Optional[str] = None,
    notes: Optional[str] = None,
    auto_categorize: bool = True,
    dedupe: bool = True,
) -> Dict[str, Any]:
    """Insert an expense claim, auto-categorising against the Books chart + backing out input VAT.
    Returns the created row plus `needs_project` (True when it couldn't be allocated to a site).
    If `dedupe` and an expense with the same amount + supplier + date already exists, returns that
    existing row with `duplicate=True` and creates nothing."""
    from vula.commerce import accounting
    db = _client()
    amount_cents = int(amount_cents or 0)

    # Sanitise the date early (also used by the dup check below).
    _d = (date or "").strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", _d):
        _d = _now()[:10]

    # Duplicate guard. The strongest key is the source receipt/media id: the SAME inbound photo
    # (Meta redelivers webhooks, and workers don't share memory) must only ever book once — and
    # this survives the OCR reading a different total/vendor each delivery. Fall back to
    # (amount + date) or (supplier + date) for a genuinely re-sent receipt.
    if dedupe:
        try:
            dup = []
            if receipt_doc_id:
                dup = (db.table("commerce_expenses").select("*").eq("tenant_id", tenant_id)
                       .eq("receipt_doc_id", receipt_doc_id).limit(1).execute().data or [])
            if not dup and amount_cents > 0:
                dup = (db.table("commerce_expenses").select("*").eq("tenant_id", tenant_id)
                       .eq("date", _d).eq("amount_cents", amount_cents).limit(1).execute().data or [])
            if not dup and amount_cents > 0 and supplier:
                dup = (db.table("commerce_expenses").select("*").eq("tenant_id", tenant_id)
                       .eq("date", _d).eq("supplier", supplier).limit(1).execute().data or [])
            if dup:
                out = dup[0]
                out["duplicate"] = True
                out["needs_project"] = False
                return out
        except Exception as exc:
            log.debug("expense dedupe check skipped: %s", exc)

    accounts = accounting.ensure_chart(tenant_id)
    txn = {"description": f"{supplier or ''} {description or ''}".strip(),
           "reference": supplier or "", "amount_cents": amount_cents}

    if not account_code:
        try:
            # An expense is never income — only offer expense accounts to the categoriser.
            exp_accounts = [a for a in accounts if a.get("type") == "expense"] or accounts
            cat = await accounting.categorize(tenant_id, txn, exp_accounts)
            account_code = cat.get("account_code")
        except Exception as exc:
            log.warning("expense categorize failed: %s", exc)
            account_code = None
    acct = next((a for a in accounts if a["code"] == account_code), None)
    category = category or (acct.get("name") if acct else "other")

    if vat_cents is None:
        try:
            vat_cents = accounting.vat_for(acct, amount_cents, accounting.is_vat_registered(tenant_id))
        except Exception:
            vat_cents = 0

    # Whose money decides reimbursement: business money (company card) is never owed back;
    # a personal card definitely is. Cash/unknown keeps the caller's flag.
    if paid_with is None and card_last4:
        paid_with = resolve_paid_with(tenant_id, card_last4=card_last4)
    if paid_with == "company_card":
        reimbursable = False
    elif paid_with == "personal":
        reimbursable = True

    row = {
        "id": str(uuid.uuid4()), "tenant_id": tenant_id,
        "date": _d,
        "description": description or (supplier or "Expense"),
        "amount_cents": amount_cents, "vat_cents": int(vat_cents or 0),
        "supplier": supplier, "supplier_id": supplier_id, "category": category, "account_code": account_code,
        "project": project, "section": section, "paid_by": paid_by, "paid_by_name": paid_by_name,
        "reimbursable": bool(reimbursable), "status": "submitted",
        "paid_with": paid_with, "card_last4": re.sub(r"\D", "", card_last4 or "")[-4:] or None,
        "channel": channel, "receipt_doc_id": receipt_doc_id, "receipt_url": receipt_url,
        "notes": notes, "source": channel, "updated_at": _now(),
    }
    # Tolerate a DB that hasn't had migrations 060/061 yet (drop the new columns, retry).
    try:
        res = db.table("commerce_expenses").insert(row).execute()
    except Exception as exc:
        log.warning("expense insert retry (pre-060/061?): %s", exc)
        for k in ("paid_by", "paid_by_name", "reimbursable", "channel", "paid_with",
                  "card_last4", "receipt_doc_id", "notes", "updated_at", "supplier_id", "section"):
            row.pop(k, None)
        res = db.table("commerce_expenses").insert(row).execute()
    out = (res.data or [row])[0]
    out["needs_project"] = (not project) and bool(known_projects(tenant_id))
    try:
        from vula.commerce import ledger
        ledger.post_expense(tenant_id, out)
    except Exception as exc:
        log.warning("ledger hook failed for expense %s: %s", out.get("id"), exc)
    return out


# ── Lifecycle ──────────────────────────────────────────────────────────────────

def list_claims(tenant_id: str, *, status: Optional[str] = None, reimbursable: Optional[bool] = None,
                project: Optional[str] = None, since: Optional[str] = None,
                until: Optional[str] = None, paid_by: Optional[str] = None, limit: int = 500) -> List[dict]:
    q = (_client().table("commerce_expenses").select("*").eq("tenant_id", tenant_id)
         .order("date", desc=True).limit(limit))
    if status:
        q = q.eq("status", status)
    if reimbursable is not None:
        q = q.eq("reimbursable", reimbursable)
    if project:
        q = q.eq("project", project)
    if since:
        q = q.gte("date", since)
    if until:
        q = q.lte("date", until)
    if paid_by:
        q = q.eq("paid_by", paid_by)
    try:
        return q.execute().data or []
    except Exception as exc:
        log.warning("list_claims failed: %s", exc)
        return []


def set_status(tenant_id: str, expense_id: str, status: str) -> dict:
    patch: Dict[str, Any] = {"status": status, "updated_at": _now()}
    if status == "reimbursed":
        patch["reimbursed_at"] = _now()
    if status == "paid":
        patch["paid_at"] = _now()
    res = (_client().table("commerce_expenses").update(patch)
           .eq("tenant_id", tenant_id).eq("id", expense_id).execute())
    return (res.data or [{}])[0]


def assign(tenant_id: str, expense_id: str, *, project: Optional[str] = None,
           account_code: Optional[str] = None, category: Optional[str] = None,
           notes: Optional[str] = None, section: Optional[str] = None,
           purpose_category: Optional[str] = None) -> dict:
    """Owner corrects/allocates a claim → apply + LEARN the account/project rule for next time."""
    db = _client()
    row = (db.table("commerce_expenses").select("*").eq("tenant_id", tenant_id)
           .eq("id", expense_id).limit(1).execute().data or [None])[0]
    patch: Dict[str, Any] = {"updated_at": _now()}
    if purpose_category:
        # A manual dashboard correction always overrides whatever auto-classify decided —
        # clears purpose_detail too, since a fresh explicit pick supersedes the earlier
        # free-text guess it was standing in for.
        if purpose_category not in PURPOSE_CATEGORIES:
            raise ValueError(f"purpose_category must be one of {PURPOSE_CATEGORIES}")
        patch["purpose_category"] = purpose_category
        patch["purpose_detail"] = None
    if project is not None:
        # 2026-08-12 fix: this used to be `project or None`, silently converting an explicit ""
        # (a real, deliberate "this has no project" answer) back into NULL (never decided) —
        # confirmed live: digg-demo had 29 expenses stuck at project IS NULL and zero at
        # project='', meaning "no project" had never once actually stuck — every dashboard/
        # WhatsApp "none" answer was silently undone. Keep the two states distinct: NULL = still
        # pending (keeps getting asked about), '' = resolved, no project.
        patch["project"] = project
    if section is not None:
        # Same NULL-vs-'' distinction as project, for the same reason (migration 129).
        patch["section"] = section
    if notes is not None:
        patch["notes"] = notes
    if account_code:
        patch["account_code"] = account_code
        if category is None and row:
            from vula.commerce import accounting
            acct = next((a for a in accounting.ensure_chart(tenant_id)
                         if a["code"] == account_code), None)
            patch["category"] = acct.get("name") if acct else category
    if category is not None:
        patch["category"] = category
    res = (db.table("commerce_expenses").update(patch)
           .eq("tenant_id", tenant_id).eq("id", expense_id).execute())
    # Learn the categorisation so similar receipts auto-file.
    if account_code and row:
        try:
            from vula.commerce import accounting
            accounting.learn_category_rule(
                tenant_id,
                {"description": f"{row.get('supplier') or ''} {row.get('description') or ''}",
                 "reference": row.get("supplier") or ""},
                account_code)
        except Exception:
            pass
    # Learn the project allocation too — same learned-rules mechanism the document-filing path
    # already uses (vula_filing_rules via doc_filing.learn_filing_rule/lookup_learned_project,
    # already fixed 2026-08-08 for cross-project-payee ambiguity). A real (non-empty) project
    # choice teaches a supplier→project signal for next time; an explicit "no project" (empty
    # string) isn't a reusable signal, so it isn't learned.
    if project and row and row.get("supplier"):
        try:
            from vula.integrations.doc_filing import learn_filing_rule
            learn_filing_rule(tenant_id, {"supplier": row["supplier"]}, project)
        except Exception:
            pass
    return (res.data or [{}])[0]


# ── Report (accountant's record + reimbursements owed) ─────────────────────────

def report(tenant_id: str, *, since: Optional[str] = None, until: Optional[str] = None,
           project: Optional[str] = None) -> Dict[str, Any]:
    rows = list_claims(tenant_id, since=since, until=until, project=project, limit=2000)
    by_cat: Dict[str, int] = {}
    by_person: Dict[str, Dict[str, Any]] = {}
    total = vat = owed = 0
    for r in rows:
        amt = int(r.get("amount_cents") or 0)
        total += amt
        vat += int(r.get("vat_cents") or 0)
        cat = r.get("category") or "other"
        by_cat[cat] = by_cat.get(cat, 0) + amt
        who = r.get("paid_by_name") or r.get("paid_by") or "—"
        p = by_person.setdefault(who, {"name": who, "total_cents": 0, "owed_cents": 0, "count": 0})
        p["total_cents"] += amt
        p["count"] += 1
        if r.get("reimbursable") and r.get("status") != "reimbursed":
            p["owed_cents"] += amt
            owed += amt
    return {
        "total_cents": total, "vat_cents": vat, "reimbursable_owed_cents": owed,
        "count": len(rows),
        "by_category": [{"category": k, "amount_cents": v} for k, v in
                        sorted(by_cat.items(), key=lambda x: -x[1])],
        "by_person": sorted(by_person.values(), key=lambda x: -x["total_cents"]),
    }
