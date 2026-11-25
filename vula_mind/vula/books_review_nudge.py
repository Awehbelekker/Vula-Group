"""vula/books_review_nudge.py — a weekly note to the owners of what's left in the books review
(25 Nov 2026; Ian: DIGG's books review hadn't moved for five days while its accountants were reading
the books).

For a business with the `books_review_nudge` switch on (vula/features.py; off by default) whose latest
books check isn't agreed yet, every Monday from 08:00 SAST each active owner gets one WhatsApp message:
what's still open, the largest first, with where to tap — and the one button that clears most
("Check again and apply all" for waiting bank statements). Outside WhatsApp's 24-hour window the same
note goes by email instead (no template: it's a reminder about the business's own books, not an
order or a booking). Once the check is agreed, nothing more is sent.

Once per owner per week: each send is kept in vula_merchant_audit (`books_review_nudged`, with the
week) and read before sending — the scheduler can run on two workers at once.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

SAST = timezone(timedelta(hours=2))
SWITCH = "books_review_nudge"
ACTION = "books_review_nudged"
SEND_WEEKDAY = 0          # Monday
SEND_HOUR = 8

# where each kind of open item is fixed, in the words of the screen
WHERE = {
    "statements": "Statements › Check again and apply all",
    "refile": "Bank lines › Filed by an old rule, one tap per payee",
    "restore": "Bank lines › Set aside by mistake", "transfers": "Bank lines › Moves between your own accounts",
    "to_categorise": "Bank lines › To categorise", "bill_payments": "Bank lines › Payments that pay a supplier's bill",
    "invoice_payments": "Bank lines › Payments from a customer", "in_transit": "In transit",
    "staff_claims": "Staff claims", "drafts": "Paperwork › Drafts", "odd_dates": "Paperwork › Odd dates",
    "no_account": "Statements: upload the missing official statements",
}


def _client():
    from vula.commerce import service
    return service._client()


def due_week(now: Optional[datetime] = None) -> Optional[str]:
    """This ISO week ('2026-W48') once it's Monday 08:00 SAST or later in the week; None before."""
    now = (now or datetime.now(SAST)).astimezone(SAST)
    if now.weekday() == SEND_WEEKDAY and now.hour < SEND_HOUR:
        return None
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def _waiting_statements(tenant_id: str) -> int:
    rows = (_client().table("vula_statement_checks").select("id").eq("tenant_id", tenant_id)
            .eq("status", "proposed").limit(500).execute().data or [])
    return len(rows)


def items(tenant_id: str) -> Optional[List[Dict[str, Any]]]:
    """What's open, largest first — None when there's nothing to nudge about (the check agreed)."""
    from vula.commerce import books_review
    from vula.commerce.year_end_pack import OPEN_LABELS
    audit = books_review.latest(tenant_id)
    if audit and audit.get("agreed_by"):
        return None
    out: List[Dict[str, Any]] = []
    waiting = _waiting_statements(tenant_id)
    if waiting:
        out.append({"key": "statements", "count": waiting, "label": "Bank statements compared and waiting to be applied"})
    try:     # statements still missing, per account (statement_files.missing)
        from vula.commerce import statement_files
        gaps = statement_files.missing(tenant_id)
    except Exception:  # noqa: BLE001
        gaps = []
    if gaps:
        out.append({"key": "missing_statements", "count": len(gaps), "label": "Bank statements still missing",
                    "detail": "; ".join(f"{g['account']}: {statement_files.ranges_text(g['ranges'])}" for g in gaps)})
    if not audit:
        out.append({"key": "run", "count": 0, "label": "The books check hasn't been run yet"})
        return out
    opened = (books_review.review(tenant_id) or {}).get("open") or {}
    for k, n in sorted(opened.items(), key=lambda kv: -int(kv[1] or 0)):
        if n:
            out.append({"key": k, "count": int(n), "label": OPEN_LABELS.get(k, k.replace("_", " ").capitalize())})
    return out


def text(business: str, open_items: List[Dict[str, Any]]) -> str:
    lines = [f"*{business} — your books review this week*"]
    if not open_items:
        lines.append("Everything is clear. Open Money › Books review › Overview and press *Agree* so your "
                     "accountant can take over.")
        return "\n".join(lines)
    for it in open_items[:6]:
        where = WHERE.get(it["key"]) or "Documents"
        if it["key"] == "run":
            lines.append("• The books check hasn't been run yet — Overview › Run the check")
        elif it["key"] == "missing_statements":
            lines.append(f"• Bank statements still missing — {it['detail']}. Send them here on WhatsApp or upload "
                         "them in Statements")
        else:
            lines.append(f"• {it['label']}: {it['count']} — {where}")
    if len(open_items) > 6:
        lines.append(f"• and {len(open_items) - 6} smaller groups")
    if any(it["key"] == "statements" for it in open_items):
        lines.append("Quickest win: Statements › *Check again and apply all* — it shows the totals before "
                     "anything changes.")
    lines.append("All in Money › Books review. When it's clear, press *Agree* on the Overview so your "
                 "accountant can take over.")
    return "\n".join(lines)


def _sent_to(tenant_id: str, week: str) -> set:
    rows = (_client().table("vula_merchant_audit").select("detail").eq("tenant_id", tenant_id)
            .eq("action", ACTION).order("created_at", desc=True).limit(200).execute().data or [])
    return {str((r.get("detail") or {}).get("to")) for r in rows if (r.get("detail") or {}).get("week") == week}


async def _email(tenant_id: str, to: str, subject: str, body: str) -> bool:
    from vula.commerce import mail_router
    if await mail_router.send_tenant_email(tenant_id, to, subject, body.replace("*", "")):
        return True
    from vula.api import email as vula_email
    plain = body.replace("*", "")
    html = "<p>" + plain.replace("\n", "<br>") + "</p>"
    return bool(await vula_email._send(to, subject, html, plain))


async def nudge(tenant_id: str, week: str) -> Dict[str, Any]:
    """This week's note to each owner who hasn't had it."""
    from vula import team_index
    from vula.api import whatsapp as wa
    from vula.api.merchant_audit import audit
    from vula.commerce import finance_folders
    open_items = await asyncio.to_thread(items, tenant_id)
    if open_items is None:
        return {"sent": [], "skipped": "the books check is agreed"}
    owners = [m for m in team_index.active_members(tenant_id)
              if (m.get("role") or "") == "owner" and (m.get("whatsapp") or m.get("email"))]
    done = _sent_to(tenant_id, week)
    business = finance_folders._business(tenant_id) or tenant_id
    note = text(business, open_items)
    sent = []
    for m in owners:
        to = str(m.get("whatsapp") or m.get("email"))
        if to in done:
            continue
        via = None
        if m.get("whatsapp") and await wa._send_reply(m["whatsapp"], note, tenant_id,
                                                      idem_key=f"booksnudge:{tenant_id}:{week}:{m['whatsapp']}"):
            via = "WhatsApp"
        elif m.get("email") and await _email(tenant_id, m["email"], f"Your books review this week – {business}", note):
            via = "email"
        if via:
            audit(tenant_id, {"email": "vula (weekly books review)"}, ACTION, week=week, to=to, via=via,
                  open=sum(it["count"] for it in open_items))
            sent.append({"name": m.get("name") or "", "via": via})
    return {"sent": sent, "items": open_items}


def _tenants() -> List[str]:
    from vula import features
    from vula.api import tenants as _t
    try:
        rows = _client().table("vula_tenant_config").select("tenant_id").execute().data or []
    except Exception as exc:  # noqa: BLE001
        log.warning("books review nudge: tenant list failed: %s", exc)
        return []
    return [r["tenant_id"] for r in rows if r.get("tenant_id") and _t.is_active(r["tenant_id"])
            and features.on(r["tenant_id"], SWITCH)]


async def tick(now: Optional[datetime] = None) -> int:
    week = due_week(now)
    if not week:
        return 0
    sent = 0
    for tid in _tenants():
        try:
            out = await nudge(tid, week)
            sent += len(out.get("sent") or [])
        except Exception as exc:  # noqa: BLE001
            log.warning("books review nudge for %s failed: %s", tid, exc)
    return sent


async def run_forever() -> None:
    await asyncio.sleep(1200)
    while True:
        try:
            await tick()
        except Exception as exc:  # noqa: BLE001
            log.warning("books review nudge tick failed: %s", exc)
        await asyncio.sleep(3600)
