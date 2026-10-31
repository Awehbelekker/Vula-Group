"""
vula/commerce/project_client.py — a project's client, talking to the business's WhatsApp line.

2026-10-28 (Ian, DIGG): "can the client do everything we did like we went through Belladonna?"
Belladonna's final account was worked out by the owner and Vula together: which bills were the
project's, who paid what, what the client still owed. A client can't make those calls — they
are changes to the business's own books — but they can ask about them, send what they paid
for, and question a line. So, for a client the owner has linked to a project (migration 233):

  ask       "what have I paid?", "what's the balance?", "the budget?", "what's left to do?" —
            answered from project_tracker.client_view() in the view the owner chose (price,
            open_book or qs), by templates, never by a model: every figure is the tracker's own
            and nothing outside the view leaves the business
  send      a slip for a supplier they paid themselves → a 'proposed' direct payment the owner
            approves or rejects; only then does it count (project_tracker.direct_payments)
  question  "what's this R14,228?" or asking for a person → the team, answered back on WhatsApp
            (vula/escalation.py, the same hand-off customers get)

A team member is never handled as a client, and a client sees only the projects they are linked
to. Money is integer cents throughout.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_TABLE = "vula_project_clients"
_DIRECT = "vula_project_direct_payments"
TASKS_SHOWN = 8


def _client():
    from vula.commerce import service
    return service._client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digits(phone: Optional[str]) -> str:
    d = re.sub(r"\D", "", phone or "")
    return "27" + d[1:] if d.startswith("0") and len(d) == 10 else d


def _key(name: Optional[str]) -> str:
    from vula.commerce.service import project_key
    return project_key(name)


# ── who is a project's client ──────────────────────────────────────────────

def list_clients(tenant_id: str, project: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = (_client().table(_TABLE).select("id,project,phone,name,view,created_by,created_at")
            .eq("tenant_id", tenant_id).is_("revoked_at", "null").execute().data or [])
    return [r for r in rows if not project or _key(r.get("project")) == _key(project)]


def links_for(tenant_id: str, phone: str) -> List[Dict[str, Any]]:
    """The projects this number is the client of (live links only)."""
    d = digits(phone)
    if not d:
        return []
    try:
        rows = (_client().table(_TABLE).select("id,project,phone,name,view")
                .eq("tenant_id", tenant_id).eq("phone", d).is_("revoked_at", "null")
                .execute().data or [])
    except Exception as exc:  # noqa: BLE001 — before migration 233 nobody is a project client
        log.debug("project client lookup skipped: %s", exc)
        return []
    return [r for r in rows if r.get("phone") == d]


def _default_view(tenant_id: str, project: str) -> str:
    """The view of the project's newest live share link, else price only."""
    try:
        from vula.commerce import project_tracker as pt
        live = [s for s in pt.list_shares(tenant_id, project) if s.get("active")]
        if live and live[0].get("view") in pt.VIEWS:
            return live[0]["view"]
    except Exception as exc:  # noqa: BLE001
        log.debug("share view lookup skipped: %s", exc)
    return "price"


def add_client(tenant_id: str, project: str, phone: str, name: Optional[str] = None,
               view: Optional[str] = None, by: Optional[str] = None) -> Dict[str, Any]:
    """Link a WhatsApp number to a project as its client, read back. Re-adding the same number
    updates its name and view."""
    from vula.commerce import project_tracker as pt
    from vula.commerce.service import registered_projects
    d = digits(phone)
    if not 9 <= len(d) <= 15:
        raise ValueError("That doesn't look like a WhatsApp number.")
    reg = {_key(r.get("name")): r.get("name") for r in registered_projects(tenant_id) if r.get("name")}
    from vula.commerce.service import canonical_project
    canon = canonical_project(tenant_id, project) or project
    proj = reg.get(_key(canon))
    if not proj:
        raise ValueError(f"There's no project called {project}.")
    try:
        from vula import team_index
        if team_index.member_for_phone(tenant_id, d):
            raise ValueError("That number is on your team — a team member can't also be a client.")
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.debug("team lookup skipped: %s", exc)
    view = view or _default_view(tenant_id, proj)
    if view not in pt.VIEWS:
        raise ValueError(f"view must be one of {pt.VIEWS}")
    clean_name = " ".join(str(name or "").split())[:80] or None
    db = _client()
    live = [r for r in links_for(tenant_id, d) if _key(r.get("project")) == _key(proj)]
    if live:
        db.table(_TABLE).update({"name": clean_name or live[0].get("name"), "view": view}) \
            .eq("tenant_id", tenant_id).eq("id", live[0]["id"]).execute()
    else:
        db.table(_TABLE).insert({"tenant_id": tenant_id, "project": proj, "phone": d, "name": clean_name,
                                 "view": view, "created_by": by}).execute()
    back = [r for r in links_for(tenant_id, d) if _key(r.get("project")) == _key(proj)]
    if not back or back[0].get("view") != view:
        raise RuntimeError("the client was not saved")
    return back[0]


def remove_client(tenant_id: str, client_id: str) -> bool:
    db = _client()
    db.table(_TABLE).update({"revoked_at": _now()}).eq("tenant_id", tenant_id).eq("id", client_id).execute()
    rows = (db.table(_TABLE).select("revoked_at").eq("tenant_id", tenant_id).eq("id", client_id)
            .limit(1).execute().data or [])
    return bool(rows and rows[0].get("revoked_at"))


def link_by_id(tenant_id: str, client_id: str) -> Optional[Dict[str, Any]]:
    rows = (_client().table(_TABLE).select("id,project,phone,name,view,revoked_at")
            .eq("tenant_id", tenant_id).eq("id", client_id).limit(1).execute().data or [])
    return rows[0] if rows and not rows[0].get("revoked_at") else None


def pick_link(links: List[Dict[str, Any]], text: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """The project a message is about: the only one, or the one it names. (None, names) when the
    client has several and named none of them."""
    if len(links) == 1:
        return links[0], []
    t = f" {_key(text)} "
    named = [lk for lk in links if f" {_key(lk.get('project'))} " in t]
    if len(named) == 1:
        return named[0], []
    return None, sorted({lk["project"] for lk in links})


# ── what the client asked ──────────────────────────────────────────────────

_QUERY = re.compile(r"\b(what(?:'s| is| was)? (?:this|that)|why (?:is|was|did|are)|query|dispute|"
                    r"don'?t (?:recognise|recognize|understand|agree)|not right|wrong|explain|"
                    r"overcharg\w*|double[- ]?charg\w*)\b", re.I)
_AMOUNT = re.compile(r"\bR\s?\d|\d[\d ,]*\.\d{2}\b|\b\d{4,}\b", re.I)
_LEFT_FIRST = re.compile(r"\b(left to do|still to do|programme|program|finish\w*|complet\w*)\b", re.I)
_LEFT = re.compile(r"\b(remaining|progress|done|when will|how long|tasks?|schedule)\b", re.I)
_OWE = re.compile(r"\b(owe|owing|balance|due|outstanding|still to pay|pay you|how much (?:more|left))\b", re.I)
_PAID = re.compile(r"\b(paid|payments?|have i paid|did i pay|received)\b", re.I)
_SUMMARY = re.compile(r"\b(budget|spent|spend|cost|where (?:are|is) (?:we|it)|status|summary|overview|"
                      r"update|account|statement|figures|numbers)\b", re.I)
_GREETING = re.compile(r"^\s*(hi|hello|hey|hallo|good (?:morning|afternoon|evening)|morning|"
                       r"thanks?|thank you|dankie|ok|okay)\b[\s!.,🙂👍🙏]*$", re.I)
_QUESTIONISH = re.compile(r"\?\s*$|^\s*(what|when|where|who|why|how|can|could|is|are|do|does|did|will|"
                          r"would|please)\b", re.I)


def intent(text: str) -> str:
    """query | owe | paid | left | summary | menu | ask_team | other — first match wins."""
    from vula.escalation import wants_human
    t = text or ""
    q = _QUERY.search(t)
    # "what's this R14,228?", "why is the balance so high?", "that's wrong" → a person answers;
    # a bare "what's this?" with no amount is too vague to hand over.
    if wants_human(t) or (q and (_AMOUNT.search(t) or not q.group(0).lower().startswith("what"))):
        return "query"
    if _LEFT_FIRST.search(t):
        return "left"
    if _OWE.search(t):
        return "owe"
    if _PAID.search(t):
        return "paid"
    if _LEFT.search(t):
        return "left"
    if _SUMMARY.search(t):
        return "summary"
    if _GREETING.match(t) or not t.strip():
        return "menu"
    return "ask_team" if _QUESTIONISH.search(t) else "menu"


def _r(cents: Optional[int]) -> str:
    from vula.commerce.project_tracker import _r as fmt
    return fmt(cents)


def _business(tenant_id: str) -> str:
    try:
        from vula.api.tenants import display_name
        return display_name(tenant_id)
    except Exception:  # noqa: BLE001
        return "the team"


def _open_tasks(tenant_id: str, project: str) -> List[Dict[str, Any]]:
    try:
        from vula.commerce.project_programme import _DONE, programme_tasks
        tasks = [t for t in programme_tasks(tenant_id, project) if t.get("status") not in _DONE]
    except Exception as exc:  # noqa: BLE001
        log.debug("programme read skipped: %s", exc)
        return []
    return sorted(tasks, key=lambda t: (str(t.get("due_date") or "9999"), t.get("title") or ""))


def _menu(biz: str, project: str) -> str:
    return (f"I can tell you about *{project}*: what you've paid, what's due, the budget, or "
            f"what's left to do. Anything else, just ask and I'll pass it to the {biz} team.")


def answer(tenant_id: str, link: Dict[str, Any], text: str) -> Dict[str, Any]:
    """{reply, escalate}: the reply to send, and whether the message goes to the team instead.
    Figures come only from project_tracker.client_view(…, link['view'])."""
    from vula.commerce import project_tracker as pt
    kind = intent(text)
    biz, name = _business(tenant_id), link["project"]
    if kind in ("query", "ask_team"):
        return {"reply": (f"I've asked the {biz} team about that — they'll reply to you here. 🙏"),
                "escalate": True, "intent": kind}
    if kind == "menu":
        hello = f"Hi {link['name'].split()[0]}! " if link.get("name") else "Hi! "
        return {"reply": hello + _menu(biz, name), "escalate": False, "intent": kind}

    view = link.get("view") if link.get("view") in pt.VIEWS else "price"
    v = pt.client_view(pt.position(tenant_id, name), view)
    project = v.get("project") or name
    lines: List[str] = []
    if kind == "left":
        open_tasks = _open_tasks(tenant_id, project)
        prog = pt._programme(tenant_id, project)
        if not prog and not open_tasks:
            return {"reply": (f"There's no programme for *{project}* in Vula yet, so I can't say what's "
                              f"left. I've asked the {biz} team to let you know."),
                    "escalate": True, "intent": kind}
        if prog:
            lines.append(f"*{project}* — {prog['done']} of {prog['tasks']} tasks done, "
                         f"{prog['time_pct']}% of the programme's time gone (finish {prog['finish']}).")
        if open_tasks:
            lines.append("Still to do:")
            for t in open_tasks[:TASKS_SHOWN]:
                where = f" ({t['room']})" if t.get("room") else ""
                due = f" — by {str(t['due_date'])[:10]}" if t.get("due_date") else ""
                lines.append(f"• {t.get('title') or 'Task'}{where}{due}")
            if len(open_tasks) > TASKS_SHOWN:
                lines.append(f"…and {len(open_tasks) - TASKS_SHOWN} more.")
        return {"reply": "\n".join(lines), "escalate": False, "intent": kind}

    open_book = view == "open_book"
    if kind == "paid":
        lines.append(f"*{project}* — what you've paid:")
        if open_book:
            lines.append(f"To {biz}: {_r(v.get('received_cents'))}")
            lines.append(f"Directly to suppliers: {_r(v.get('paid_directly_cents'))}")
            recent = [p for p in v.get("direct_payments") or [] if p.get("status") == "paid"][-5:]
            for p in recent:
                when = f" ({p['paid_on']})" if p.get("paid_on") else ""
                lines.append(f"• {p.get('supplier')}: {_r(p.get('amount_cents'))}{when}")
        else:
            lines.append(f"Invoices paid: {_r(v.get('invoices_paid_cents'))} of {_r(v.get('invoiced_cents'))} invoiced")
    elif kind == "owe":
        lines.append(f"*{project}* — what's due:")
        if open_book:
            lines.append(f"{biz}'s cost to date: {_r(v.get('business_cost_cents'))}")
            lines.append(f"Fee ({v.get('fee_pct') or 0:g}%): {_r(v.get('fee_cents'))}")
            lines.append(f"Paid to {biz} so far: {_r(v.get('received_cents'))}")
            lines.append(f"*Balance due: {_r(v.get('open_book_balance_cents'))}*")
            if v.get("to_pay_directly_cents"):
                lines.append(f"Still to pay suppliers directly: {_r(v['to_pay_directly_cents'])}")
        else:
            lines.append(f"*Owed now: {_r(v.get('invoices_owed_cents'))}*")
            unpaid = [i for i in v.get("invoices") or [] if (i.get("total_cents") or 0) > (i.get("paid_cents") or 0)]
            for i in unpaid[:5]:
                due = f", due {i['due_date']}" if i.get("due_date") else ""
                lines.append(f"• {i.get('invoice_number')}: {_r((i.get('total_cents') or 0) - (i.get('paid_cents') or 0))}{due}")
            if v.get("to_invoice_cents"):
                lines.append(f"Still to invoice on the contract: {_r(v['to_invoice_cents'])}")
    else:   # summary
        lines.append(f"*{project}* — where it stands ({v.get('as_of')}):")
        if v.get("contract_cents"):
            lines.append(f"Contract: {_r(v['contract_cents'])}"
                         + (f" (incl. variations {_r(v['variations_claimed_cents'])})"
                            if v.get("variations_claimed_cents") else ""))
        if open_book:
            lines.append(f"Spent so far: {_r(v.get('spent_cents'))}")
            if v.get("budget_left_cents") is not None:
                lines.append(f"Budget left: {_r(v['budget_left_cents'])}")
            lines.append(f"{biz}'s cost: {_r(v.get('business_cost_cents'))} · paid directly by you: "
                         f"{_r(v.get('paid_directly_cents'))}")
            lines.append(f"*Balance due to {biz}: {_r(v.get('open_book_balance_cents'))}*")
        else:
            lines.append(f"Invoiced: {_r(v.get('invoiced_cents'))} · paid: {_r(v.get('invoices_paid_cents'))}")
            lines.append(f"*Owed now: {_r(v.get('invoices_owed_cents'))}*")
            if v.get("to_invoice_cents"):
                lines.append(f"Still to invoice: {_r(v['to_invoice_cents'])}")
        if view == "qs" and v.get("programme"):
            pg = v["programme"]
            lines.append(f"Programme: {pg['done']} of {pg['tasks']} tasks done")
    return {"reply": "\n".join(lines), "escalate": False, "intent": kind}


# ── a slip the client sends ────────────────────────────────────────────────

def stage_slip(tenant_id: str, link: Dict[str, Any], *, supplier: str, amount_cents: int,
               paid_on: Optional[str] = None, reference: Optional[str] = None) -> Dict[str, Any]:
    """A payment the client says they made to a supplier, held as 'proposed' until the owner
    approves it. Read back."""
    supplier = " ".join(str(supplier or "").split())[:120] or "a supplier"
    amount_cents = int(amount_cents)
    if amount_cents <= 0:
        raise ValueError("no amount on the slip")
    who = link.get("name") or "the client"
    desc = f"Slip sent by {who} on WhatsApp" + (f", ref {reference}" if reference else "")
    db = _client()
    res = db.table(_DIRECT).insert({
        "tenant_id": tenant_id, "project": link["project"], "supplier": supplier,
        "description": desc[:200], "amount_cents": amount_cents,
        "paid_on": (str(paid_on)[:10] if paid_on else None), "status": "proposed",
        "created_by": f"client:{link['id']}"}).execute()
    new_id = ((getattr(res, "data", None) or [{}])[0] or {}).get("id")
    rows = (db.table(_DIRECT).select("id,project,supplier,amount_cents,paid_on,status,created_by")
            .eq("tenant_id", tenant_id).eq("id", new_id).limit(1).execute().data or []) if new_id else []
    if not rows or rows[0].get("status") != "proposed":
        raise RuntimeError("the slip was not saved")
    return rows[0]


def decide_slip(tenant_id: str, payment_id: str, approve: bool, *, by_phone: Optional[str] = None,
                source: str = "whatsapp") -> Optional[Dict[str, Any]]:
    """The owner's answer to a client's slip: 'proposed' → 'paid' (counts from now) or 'removed'.
    None when it was already decided. Read back, then kept in the decision log."""
    db = _client()
    rows = (db.table(_DIRECT).select("id,project,supplier,amount_cents,paid_on,status,created_by")
            .eq("tenant_id", tenant_id).eq("id", payment_id).limit(1).execute().data or [])
    if not rows or rows[0].get("status") != "proposed":
        return None
    db.table(_DIRECT).update({"status": "paid" if approve else "removed", "updated_at": _now()}) \
        .eq("tenant_id", tenant_id).eq("id", payment_id).eq("status", "proposed").execute()
    back = (db.table(_DIRECT).select("id,project,supplier,amount_cents,paid_on,status,created_by")
            .eq("tenant_id", tenant_id).eq("id", payment_id).limit(1).execute().data or [])
    want = "paid" if approve else "removed"
    if not back or back[0].get("status") != want:
        raise RuntimeError("the decision was not saved")
    r = back[0]
    try:
        from vula.commerce import decisions
        decisions.record(
            tenant_id, (f"The client's slip for {r['supplier']} {_r(r['amount_cents'])} is accepted: "
                        "recorded as paid by the client directly." if approve else
                        f"The client's slip for {r['supplier']} {_r(r['amount_cents'])} is left out."),
            project=r.get("project"), subject="Client's slip", amount_cents=r.get("amount_cents"),
            refs=[{"table": _DIRECT, "id": r["id"]}], phone=by_phone, role="owner", source=source)
    except Exception as exc:  # noqa: BLE001 — the slip is decided either way
        log.debug("slip decision not logged: %s", exc)
    return r


def proposed_slips(tenant_id: str, project: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = (_client().table(_DIRECT).select("id,project,supplier,description,amount_cents,paid_on,created_at,created_by")
            .eq("tenant_id", tenant_id).eq("status", "proposed").execute().data or [])
    return [r for r in rows if not project or _key(r.get("project")) == _key(project)]


def slip_prompt(link: Dict[str, Any], row: Dict[str, Any], file_url: Optional[str] = None) -> str:
    who = link.get("name") or f"The client ({link.get('phone')})"
    when = f" on {row['paid_on']}" if row.get("paid_on") else ""
    return (f"🧾 {who} sent a slip for *{link['project']}*: {_r(row['amount_cents'])} paid directly to "
            f"{row['supplier']}{when}."
            + (f"\n{file_url}" if file_url else "")
            + "\n\nReply *yes* to record it as paid by the client (it counts in the project from now), "
              "or *no* to leave it out.")
