"""
vula/commerce/project_tracker.py — one project's money, for the owner and, by link, its client.

2026-10-27 (Ian, DIGG): Belladonna's final account — budget against actual, what Rudi paid
suppliers directly against what he paid DIGG, labour against materials, and the balance due —
was built by hand from the bank, the filed invoices and Judy's sheet over a dozen rounds of
corrections. Everything it needed is already in Vula; this puts it in one place, from the same
sources the rest of Vula uses (no second set of books):

  budget            vula_project_boq: the bill's total and its sections (the signed baseline
                    when one is locked — project_programme.set_baseline)
  variations        documents labelled as variations (job_costing._variations): extra work
                    claimed from the client, extra charged by suppliers
  cost by trade     the business's bank lines allocated to the project (job_costing.costing),
                    plus statement-proven claims someone paid from their own pocket
  paid directly     what the client paid suppliers themselves: direct-supplier invoices
                    (job_costing.direct_costs) and slips answered "the client paid"
                    (commerce_expenses.paid_with = 'client') — in the project's spend, never
                    in the business's cost, fee or profit
  fee               the project's terms (vula_project_terms: cost-plus, fee %) on the business's cost
  received          money in on the project (job_costing)
  invoiced / paid   the project's own invoices (commerce_invoices)

Shared by link (vula_project_shares, migrations 226–227), in the view chosen for that project:
  price      the contract, what was invoiced and paid, what is still owed — the business's own
             costs, fee and margin stay private
  open_book  cost-plus: the business's cost per trade, what the client paid suppliers directly,
             the fee, what the client paid the business, and the balance
  qs         a quantity surveyor's view: contract, certified and anticipated final per bill,
             variations, valuations and retention, invoices, the programme, and the document
             register — the business's own costs, fee and margin stay private, as in `price`

The link's token is random (secrets.token_urlsafe) and only its sha256 is stored; every link
expires (180 days) and can be revoked. All money is integer cents; nothing is estimated.
"""
from __future__ import annotations

import hashlib
import html
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

VIEWS = ("price", "open_book", "qs")
VIEW_LABEL = {"price": "Price only", "open_book": "Open book (cost-plus)", "qs": "QS view"}
# A valuation's per-bill figures, kept on vula_project_boq sections when the BOQ is a payment
# certificate (vula/ingestion/boq_sheet.py: certified / anticipated / variation columns).
_SECTION_EXTRA = ("certified_cents", "anticipated_cents", "variation_cents")
LINK_DAYS = 180


def _client():
    from vula.commerce import service
    return service._client()


def _key(name: Optional[str]) -> str:
    from vula.commerce.service import project_key
    return project_key(name)


def _rows(make) -> List[Dict[str, Any]]:
    from vula.commerce.ledger import _all_pages
    try:
        return _all_pages(make)
    except Exception as exc:
        log.debug("project tracker read skipped: %s", exc)
        return []


def _boq(tenant_id: str, key: str) -> Optional[Dict[str, Any]]:
    rows = _rows(lambda: _client().table("vula_project_boq")
                 .select("project,title,total_cents,sections,baseline_locked,baseline_set_at")
                 .eq("tenant_id", tenant_id))
    return next((r for r in rows if _key(r.get("project")) == key), None)


def _client_paid(tenant_id: str, key: str) -> Dict[str, Any]:
    """Slips the client paid the supplier for directly ("the client paid" — whatsapp.py)."""
    rows = _rows(lambda: _client().table("commerce_expenses")
                 .select("date,supplier,description,amount_cents,project")
                 .eq("tenant_id", tenant_id).eq("paid_with", "client"))
    mine = [r for r in rows if _key(r.get("project")) == key]
    return {"cents": sum(int(r.get("amount_cents") or 0) for r in mine), "lines": len(mine)}


def _invoices(tenant_id: str, key: str) -> List[Dict[str, Any]]:
    rows = _rows(lambda: _client().table("commerce_invoices")
                 .select("invoice_number,issue_date,due_date,total_cents,total_paid_cents,status,"
                         "doc_type,direction,project")
                 .eq("tenant_id", tenant_id).eq("doc_type", "invoice"))
    out = []
    for r in rows:
        if _key(r.get("project")) != key or (r.get("direction") or "outbound") != "outbound" \
                or r.get("status") in ("cancelled", "draft"):
            continue
        total = int(r.get("total_cents") or 0)
        paid = total if r.get("status") == "paid" else min(total, int(r.get("total_paid_cents") or 0))
        out.append({"number": r.get("invoice_number"), "date": r.get("issue_date"), "due": r.get("due_date"),
                    "total_cents": total, "paid_cents": paid, "owed_cents": total - paid,
                    "status": r.get("status")})
    return sorted(out, key=lambda i: str(i.get("date") or ""))


def _claims(tenant_id: str, project: str) -> Dict[str, Any]:
    """The project's progress claims (vula_project_claims): how many, and the latest's figures."""
    try:
        from vula.integrations.progress_claims import list_claims
        rows = list_claims(tenant_id, project)
    except Exception as exc:
        log.debug("claims read skipped: %s", exc)
        rows = []
    if not rows:
        return {"count": 0, "latest": None}
    last = rows[-1]
    keys = ("cumulative_value_cents", "certified_to_date_cents", "retention_cents", "this_payment_cents")
    return {"count": len(rows), "latest": {"number": last.get("claim_number"), "status": last.get("status"),
                                           "date": last.get("claim_date"),
                                           **{k: int(last.get(k) or 0) for k in keys}}}


def _programme(tenant_id: str, project: str, today: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Time gone on the programme and tasks done (project_programme's field tasks)."""
    from datetime import date
    try:
        from vula.commerce.project_programme import programme_tasks
        tasks = programme_tasks(tenant_id, project)
    except Exception as exc:
        log.debug("programme read skipped: %s", exc)
        return None
    dates = sorted(str(x)[:10] for t in tasks for x in (t.get("start_date"), t.get("due_date")) if x)
    if not tasks or not dates:
        return None
    first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    day = date.fromisoformat(today) if today else datetime.now(timezone.utc).date()
    span = max((last - first).days + 1, 1)
    done = sum(1 for t in tasks if t.get("status") == "complete")
    return {"tasks": len(tasks), "done": done, "done_pct": round(100 * done / len(tasks)),
            "time_pct": max(0, min(100, round(100 * ((day - first).days + 1) / span))),
            "start": first.isoformat(), "finish": last.isoformat()}


def _documents(tenant_id: str, key: str, limit: int = 60) -> List[Dict[str, Any]]:
    """The project's document register: what was filed, as what, when (names only — no files)."""
    rows = _rows(lambda: _client().table("vula_filed_documents").select("filename,category,created_at,project,status")
                 .eq("tenant_id", tenant_id).order("created_at", desc=True))
    mine = [r for r in rows if _key(r.get("project")) == key and r.get("status") != "deleted"]
    return [{"name": r.get("filename"), "category": r.get("category"), "filed": str(r.get("created_at") or "")[:10]}
            for r in mine[:limit]]


def _fee_pct(tenant_id: str, project: str, costed: Optional[Dict[str, Any]]) -> float:
    if costed and costed.get("fee_pct") is not None:
        return float(costed["fee_pct"])
    from vula.commerce import job_costing
    t = job_costing.terms(tenant_id)
    return next((v for p, v in t.items() if p != "*" and _key(p) == _key(project)), t["*"])


def position(tenant_id: str, project: str) -> Dict[str, Any]:
    """The project's whole money picture (the owner's view). Every figure in cents."""
    from vula.commerce import job_costing
    key = _key(project)
    costing = job_costing.costing(tenant_id)
    costed = next((p for p in costing.get("projects") or [] if _key(p["project"]) == key), None)
    boq = _boq(tenant_id, key) or {}
    name = (costed or {}).get("project") or boq.get("project") or project
    business_cost = int((costed or {}).get("cost_cents") or 0)
    received = int((costed or {}).get("received_cents") or 0)
    variations = (costed or {}).get("variations") or {}
    direct = int((job_costing.direct_costs(tenant_id).get(key) or {}).get("cents") or 0)
    by_client = _client_paid(tenant_id, key)
    invoices = _invoices(tenant_id, key)
    fee_pct = _fee_pct(tenant_id, name, costed)
    fee = int(round(business_cost * fee_pct / 100))
    budget = int(boq.get("total_cents") or 0)
    claimed = int(variations.get("claimed_cents") or 0)
    contract = budget + claimed
    invoiced = sum(i["total_cents"] for i in invoices)
    paid = sum(i["paid_cents"] for i in invoices)
    paid_directly = direct + by_client["cents"]
    spent = business_cost + paid_directly
    return {
        "project": name,
        "budget_cents": budget, "budget_title": boq.get("title"),
        "baseline_locked": bool(boq.get("baseline_locked")),
        "sections": [{"section": s.get("section"), "budget_cents": int(s.get("budget_cents") or 0),
                      **{k: int(s[k]) for k in _SECTION_EXTRA if s.get(k) is not None}}
                     for s in boq.get("sections") or [] if s.get("section")],
        "variations_claimed_cents": claimed,
        "variations_extra_cost_cents": int(variations.get("extra_cost_cents") or 0),
        "contract_cents": contract,
        "trades": [{"trade": t["trade"], "cents": int(t["cents"])} for t in (costed or {}).get("trades") or []],
        "business_cost_cents": business_cost,
        "paid_directly_cents": paid_directly,
        "paid_directly_suppliers_cents": direct, "paid_directly_slips_cents": by_client["cents"],
        "spent_cents": spent,
        "budget_left_cents": budget - spent if budget else None,
        "spent_pct": round(100 * spent / budget) if budget else None,
        "fee_pct": fee_pct, "fee_cents": fee,
        "open_book_total_cents": business_cost + fee,
        "received_cents": received,
        "open_book_balance_cents": business_cost + fee - received,
        "invoices": invoices, "invoiced_cents": invoiced, "invoices_paid_cents": paid,
        "invoices_owed_cents": invoiced - paid,
        "to_invoice_cents": max(contract - invoiced, 0) if contract else None,
        "claims": _claims(tenant_id, name),
        "programme": _programme(tenant_id, name),
        "documents": _documents(tenant_id, key),
        # the owner's own figures — never in a client view
        "overhead_share_cents": (costed or {}).get("overhead_share_cents"),
        "profit_cents": (costed or {}).get("profit_cents"),
        "unallocated_trade_cents": (costed or {}).get("unallocated_trade_cents") or 0,
        "as_of": datetime.now(timezone.utc).date().isoformat(),
    }


# Figures each client view may carry. Anything not listed never leaves the business.
_PRICE_FIELDS = ("project", "budget_title", "sections", "budget_cents", "variations_claimed_cents",
                 "contract_cents", "invoices", "invoiced_cents", "invoices_paid_cents",
                 "invoices_owed_cents", "to_invoice_cents", "as_of")
_QS_FIELDS = _PRICE_FIELDS + ("variations_extra_cost_cents", "baseline_locked", "claims", "programme",
                               "documents")
_OPEN_BOOK_FIELDS = _PRICE_FIELDS + ("trades", "business_cost_cents", "paid_directly_cents", "spent_cents",
                                     "budget_left_cents", "fee_pct", "fee_cents", "open_book_total_cents",
                                     "received_cents", "open_book_balance_cents")


def client_view(pos: Dict[str, Any], view: str) -> Dict[str, Any]:
    """What the client sees. `price` keeps the business's cost, fee and margin private; neither
    view carries the overhead share, profit or anything not listed above."""
    fields = {"open_book": _OPEN_BOOK_FIELDS, "qs": _QS_FIELDS}.get(view, _PRICE_FIELDS)
    return {"view": view if view in VIEWS else "price", **{k: pos.get(k) for k in fields}}


# ── share links ────────────────────────────────────────────────────────────

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_url(token: str) -> str:
    from config import settings
    return f"{settings.public_base_url.rstrip('/')}/track/{token}"


def create_share(tenant_id: str, project: str, view: str, by: Optional[str] = None) -> Dict[str, Any]:
    """A new link for the project in `view`. Read back before it's reported."""
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}")
    from vula.commerce.service import canonical_project
    name = canonical_project(tenant_id, project) or project
    token = secrets.token_urlsafe(24)
    expires = (datetime.now(timezone.utc) + timedelta(days=LINK_DAYS)).isoformat()
    db = _client()
    db.table("vula_project_shares").insert({
        "tenant_id": tenant_id, "project": name, "view": view, "token_hash": _hash(token),
        "created_by": by, "expires_at": expires}).execute()
    rows = (db.table("vula_project_shares").select("id,project,view,expires_at")
            .eq("tenant_id", tenant_id).eq("token_hash", _hash(token)).limit(1).execute().data or [])
    if not rows:
        raise RuntimeError("the share link was not saved")
    return {**rows[0], "url": public_url(token)}


def list_shares(tenant_id: str, project: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = _rows(lambda: _client().table("vula_project_shares")
                 .select("id,project,view,created_by,created_at,expires_at,revoked_at,last_viewed_at,views")
                 .eq("tenant_id", tenant_id).order("created_at", desc=True))
    now = datetime.now(timezone.utc).isoformat()
    out = [{**r, "active": not r.get("revoked_at") and str(r.get("expires_at") or "") > now} for r in rows]
    return [r for r in out if not project or _key(r["project"]) == _key(project)]


def revoke_share(tenant_id: str, share_id: str) -> bool:
    db = _client()
    db.table("vula_project_shares").update({"revoked_at": datetime.now(timezone.utc).isoformat()}) \
        .eq("tenant_id", tenant_id).eq("id", share_id).execute()
    rows = (db.table("vula_project_shares").select("revoked_at")
            .eq("tenant_id", tenant_id).eq("id", share_id).limit(1).execute().data or [])
    return bool(rows and rows[0].get("revoked_at"))


def share_for_token(token: str) -> Optional[Dict[str, Any]]:
    """The live share a token opens, or None (unknown, revoked or expired)."""
    if not token or len(token) > 100:
        return None
    try:
        rows = (_client().table("vula_project_shares").select("id,tenant_id,project,view,expires_at,revoked_at,views")
                .eq("token_hash", _hash(token)).limit(1).execute().data or [])
    except Exception as exc:
        log.warning("project share lookup failed: %s", exc)
        return None
    if not rows or rows[0].get("revoked_at") or str(rows[0].get("expires_at") or "") <= datetime.now(timezone.utc).isoformat():
        return None
    return rows[0]


def note_view(share: Dict[str, Any]) -> None:
    try:
        _client().table("vula_project_shares").update(
            {"views": int(share.get("views") or 0) + 1, "last_viewed_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", share["id"]).execute()
    except Exception as exc:
        log.debug("project share view count skipped: %s", exc)


# ── text and page ──────────────────────────────────────────────────────────

def _r(cents: Optional[int]) -> str:
    if cents is None:
        return "—"
    return ("-" if cents < 0 else "") + f"R{abs(cents) / 100:,.2f}".replace(",", " ")


def summary_text(pos: Dict[str, Any], view: str) -> str:
    """WhatsApp text of what a client link in `view` will show (the owner's preview)."""
    v = client_view(pos, view)
    lines = [f"*{v['project']}* — what the client will see ({VIEW_LABEL[v['view']]}):"]
    if v.get("contract_cents"):
        lines.append(f"Contract: {_r(v['contract_cents'])}"
                     + (f" (incl. variations {_r(v['variations_claimed_cents'])})" if v.get("variations_claimed_cents") else ""))
    if view == "open_book":
        lines += [f"Your cost to date: {_r(v['business_cost_cents'])}",
                  f"Paid by the client directly to suppliers: {_r(v['paid_directly_cents'])}",
                  f"Fee ({v['fee_pct']:g}%): {_r(v['fee_cents'])}",
                  f"Paid to you so far: {_r(v['received_cents'])}",
                  f"*Balance due: {_r(v['open_book_balance_cents'])}*"]
    else:
        if view == "qs":
            c = (v.get("claims") or {}).get("latest")
            if c:
                lines.append(f"Valuation {c['number']}: certified to date {_r(c['certified_to_date_cents'])}, "
                             f"retention {_r(c['retention_cents'])}")
            certified = sum(s.get("certified_cents") or 0 for s in v.get("sections") or [])
            if certified:
                lines.append(f"Certified per the bill: {_r(certified)}")
            if v.get("programme"):
                pg = v["programme"]
                lines.append(f"Programme: {pg['time_pct']}% of the time gone, {pg['done']} of {pg['tasks']} tasks done")
            lines.append(f"Documents in the register: {len(v.get('documents') or [])}")
        lines += [f"Invoiced: {_r(v['invoiced_cents'])}", f"Paid: {_r(v['invoices_paid_cents'])}",
                  f"*Owed now: {_r(v['invoices_owed_cents'])}*"]
        if v.get("to_invoice_cents") is not None:
            lines.append(f"Still to invoice: {_r(v['to_invoice_cents'])}")
    return "\n".join(lines)


def render(brand: Dict[str, Any], v: Dict[str, Any]) -> str:
    """The client's page. Every value escaped; every figure from client_view()."""
    from vula.commerce import powered_by
    e = html.escape
    name = e(brand.get("name") or "")
    accent = e(brand.get("accent_color") or "#2C5545")
    logo = brand.get("logo_url")
    logo_html = f"<img src='{e(logo)}' alt='{name}' class='logo'>" if logo else f"<h2>{name}</h2>"

    def row(label, cents, strong=False):
        cls = " class='total'" if strong else ""
        return f"<tr{cls}><td>{e(label)}</td><td class='n'>{_r(cents)}</td></tr>"

    def table(title, rows):
        return f"<h3>{e(title)}</h3><table>{''.join(rows)}</table>" if rows else ""

    parts = []
    head = [row("Agreed budget", v.get("budget_cents"))] if v.get("budget_cents") else []
    if v.get("variations_claimed_cents"):
        head.append(row("Variations", v["variations_claimed_cents"]))
    if v.get("contract_cents"):
        head.append(row("Contract total", v["contract_cents"], True))
    parts.append(table("The project", head))
    if v["view"] == "open_book":
        cost = [row(t["trade"], t["cents"]) for t in v.get("trades") or []]
        cost.append(row("Cost through us", v.get("business_cost_cents"), True))
        parts.append(table("Spent through us, by trade", cost))
        acct = [row("Cost through us", v.get("business_cost_cents")),
                row(f"Fee ({v.get('fee_pct') or 0:g}%)", v.get("fee_cents")),
                row("Total for our part", v.get("open_book_total_cents"), True),
                row("Paid to us so far", -(v.get("received_cents") or 0)),
                row("Balance due", v.get("open_book_balance_cents"), True)]
        parts.append(table("Account", acct))
        if v.get("paid_directly_cents"):
            spent = [row("Through us", v.get("business_cost_cents")),
                     row("Paid by you directly to suppliers", v.get("paid_directly_cents")),
                     row("Total spent on the project", v.get("spent_cents"), True)]
            if v.get("budget_left_cents") is not None:
                spent.append(row("Budget left", v["budget_left_cents"]))
            parts.append(table("Against the budget", spent))
    else:
        acct = [row("Invoiced to date", v.get("invoiced_cents")), row("Paid", v.get("invoices_paid_cents")),
                row("Owed now", v.get("invoices_owed_cents"), True)]
        if v.get("to_invoice_cents") is not None:
            acct.append(row("Still to invoice", v["to_invoice_cents"]))
        parts.append(table("Account", acct))
    secs = v.get("sections") or []
    if secs and v["view"] == "qs" and any("certified_cents" in s or "anticipated_cents" in s for s in secs):
        cell = lambda c: f"<td class='n'>{_r(c)}</td>"
        body = [f"<tr><td>{e(s['section'])}</td>{cell(s['budget_cents'])}{cell(s.get('certified_cents'))}"
                f"{cell(s.get('anticipated_cents'))}</tr>" for s in secs]
        tot = lambda k: sum(s.get(k) or 0 for s in secs)
        body.append(f"<tr class='total'><td>Total</td>{cell(tot('budget_cents'))}{cell(tot('certified_cents'))}"
                    f"{cell(tot('anticipated_cents'))}</tr>")
        parts.append("<h3>By bill</h3><table><tr class='h'><td></td><td class='n'>Contract</td><td class='n'>Certified</td>"
                     f"<td class='n'>Anticipated final</td></tr>{''.join(body)}</table>")
    elif secs:
        parts.append(table("Budget by section", [row(s["section"], s["budget_cents"]) for s in secs]))
    if v["view"] == "qs":
        c = (v.get("claims") or {}).get("latest")
        if c:
            parts.append(table(f"Valuation {c['number']} ({c.get('status') or ''})", [
                row("Value of work to date", c["cumulative_value_cents"]),
                row("Retention held", c["retention_cents"]),
                row("Certified to date", c["certified_to_date_cents"], True),
                row("This payment", c["this_payment_cents"])]))
        if v.get("variations_extra_cost_cents"):
            parts.append(table("Variations", [row("Claimed from the client", v.get("variations_claimed_cents")),
                                              row("Extra charged by suppliers", v["variations_extra_cost_cents"])]))
        pg = v.get("programme")
        if pg:
            parts.append(f"<h3>Programme</h3><p class='muted'>{e(pg['start'])} to {e(pg['finish'])}: "
                         f"{pg['time_pct']}% of the time gone · {pg['done']} of {pg['tasks']} tasks done "
                         f"({pg['done_pct']}%)</p>")
        docs = v.get("documents") or []
        if docs:
            parts.append(table(f"Document register ({len(docs)})", [
                f"<tr><td>{e(str(d.get('name') or ''))}<span class='d'>{e(str(d.get('category') or ''))}</span></td>"
                f"<td class='n'>{e(d.get('filed') or '')}</td></tr>" for d in docs]))
    inv = [f"<tr><td>{e(str(i.get('number') or ''))}<span class='d'>{e(str(i.get('date') or ''))}</span></td>"
           f"<td class='n'>{_r(i['total_cents'])}<span class='d'>{'paid' if i['owed_cents'] <= 0 else _r(i['owed_cents']) + ' owed'}</span></td></tr>"
           for i in v.get("invoices") or []]
    parts.append(table("Invoices", inv))
    title = e(v.get("project") or "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{title} · {name}</title><style>
body{{margin:0;padding:0 12px;background:#f5f3ee;font-family:system-ui,-apple-system,sans-serif;color:#2a2a2a}}
.card{{max-width:560px;margin:28px auto;background:#fff;border-radius:14px;padding:24px 22px;box-shadow:0 2px 10px rgba(0,0,0,.06)}}
.logo{{max-height:56px;max-width:200px}}h1{{font-size:22px;margin:14px 0 2px;color:{accent}}}
.muted{{color:#6e6a63;margin:0 0 8px;font-size:14px}}h3{{font-size:15px;margin:22px 0 6px;color:{accent}}}
table{{width:100%;border-collapse:collapse;font-size:15px}}td{{padding:7px 0;border-bottom:1px solid #eee;vertical-align:top}}
td.n{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}tr.total td{{font-weight:700;border-top:2px solid {accent}}}
.d{{display:block;color:#8a857c;font-size:12px}}tr.h td{{color:#8a857c;font-size:12px}}
{powered_by.pill_css(dark_mode=False)}
</style></head><body><div class="card">{logo_html}<h1>{title}</h1>
<p class="muted">Project account · {e(VIEW_LABEL[v['view']])} · as of {e(str(v.get('as_of') or ''))}</p>
{''.join(parts)}<p class="muted" style="margin-top:20px">Questions? Contact {name}.</p></div>{powered_by.pill_html()}</body></html>"""
