"""Weekly advisor message for each owner (2026-09-30).

Ian: "Can Vula recommend?" — it had price advice and job-costing alerts, but nothing that looked
across the business and said what needs attention. Monday mornings each owner gets one short
WhatsApp built ONLY from real rows — never a model, never a figure that isn't in the database:

  money owed      customer invoices past their due date, biggest first
  spending        bank categories this month well above their usual monthly level
  low stock       tracked products at or below their reorder point (shops only)
  unanswered      questions Vula had to hand to the team this week, and any unanswered
  profile gaps    business-profile questions still blank (Vula says "I'll check" for those)
  waiting         documents filed but not yet put on a project

A section with nothing to say is left out; a week with nothing at all sends nothing.
Gated by settings.owner_advisor_enabled (off until previewed in Master › Conversations).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)
SAST = timezone(timedelta(hours=2))


def _client():
    from vula.commerce import service
    return service._client()


def _r(cents: int) -> str:
    return f"R{cents / 100:,.0f}" if abs(cents) >= 100_000 else f"R{cents / 100:,.2f}"


def overdue_invoices(tenant_id: str, today: date) -> Dict[str, Any]:
    try:
        rows = (_client().table("commerce_invoices")
                .select("invoice_number,customer_name,total_cents,total_paid_cents,due_date,status")
                .eq("tenant_id", tenant_id).eq("direction", "outbound")
                .in_("status", ["sent", "overdue", "partially_paid", "partial"])
                .lt("due_date", today.isoformat()).limit(500).execute().data or [])
    except Exception as exc:
        log.debug("advisor: invoices skipped: %s", exc)
        return {}
    owed = []
    for r in rows:
        left = int(r.get("total_cents") or 0) - int(r.get("total_paid_cents") or 0)
        if left > 0:
            days = (today - date.fromisoformat(str(r["due_date"])[:10])).days
            owed.append({"who": r.get("customer_name") or "?", "ref": r.get("invoice_number"),
                         "cents": left, "days": days})
    if not owed:
        return {}
    owed.sort(key=lambda x: -x["cents"])
    return {"count": len(owed), "total_cents": sum(o["cents"] for o in owed), "top": owed[:3]}


def spending_jumps(tenant_id: str, today: date) -> List[Dict[str, Any]]:
    """Categories whose last-30-day spend is ≥40% and ≥R2,000 above their monthly average over
    the 90 days before that. Needs bank lines in both windows (statements arrive irregularly)."""
    start = today - timedelta(days=120)
    try:
        rows = (_client().table("commerce_bank_transactions")
                .select("txn_date,amount_cents,account_code")
                .eq("tenant_id", tenant_id).eq("direction", "out")
                .gte("txn_date", start.isoformat()).lte("txn_date", today.isoformat())
                .limit(5000).execute().data or [])
    except Exception as exc:
        log.debug("advisor: bank skipped: %s", exc)
        return []
    recent, before = defaultdict(int), defaultdict(int)
    cut = (today - timedelta(days=30)).isoformat()
    has_before = False
    for r in rows:
        code = r.get("account_code")
        if not code:
            continue
        amt = abs(int(r.get("amount_cents") or 0))
        if str(r.get("txn_date")) >= cut:
            recent[code] += amt
        else:
            before[code] += amt
            has_before = True
    if not has_before or not recent:
        return []
    out = []
    for code, now_c in recent.items():
        usual = before.get(code, 0) / 3
        if usual > 0 and now_c >= usual * 1.4 and now_c - usual >= 200_000:
            out.append({"category": code.replace("_", " "), "now_cents": now_c,
                        "usual_cents": int(usual), "pct": round(100 * (now_c - usual) / usual)})
    out.sort(key=lambda x: -(x["now_cents"] - x["usual_cents"]))
    return out[:3]


def low_stock(tenant_id: str) -> List[Dict[str, Any]]:
    try:
        rows = (_client().table("commerce_products")
                .select("name,stock_quantity,reorder_threshold,archived")
                .eq("tenant_id", tenant_id).not_.is_("stock_quantity", "null")
                .limit(1000).execute().data or [])
    except Exception as exc:
        log.debug("advisor: stock skipped: %s", exc)
        return []
    out = [{"name": r["name"], "qty": r["stock_quantity"], "at": r["reorder_threshold"]}
           for r in rows if not r.get("archived") and r.get("reorder_threshold") is not None
           and r["stock_quantity"] <= r["reorder_threshold"]]
    return sorted(out, key=lambda x: x["qty"])[:5]


def unanswered(tenant_id: str, today: date) -> Dict[str, Any]:
    since = (datetime.combine(today, datetime.min.time(), tzinfo=SAST) - timedelta(days=7)).isoformat()
    try:
        rows = (_client().table("vula_escalations").select("question,status")
                .eq("tenant_id", tenant_id).gte("created_at", since).limit(200).execute().data or [])
    except Exception as exc:
        log.debug("advisor: escalations skipped: %s", exc)
        return {}
    if not rows:
        return {}
    open_ = [r for r in rows if r.get("status") in ("open", "expired")]
    return {"count": len(rows), "unanswered": len(open_),
            "examples": [(r.get("question") or "")[:80] for r in open_[:3]]}


def profile_gaps(tenant_id: str) -> int:
    try:
        from vula.commerce.business_profile import status
        return len(status(tenant_id).get("missing") or [])
    except Exception:
        return 0


def waiting_documents(tenant_id: str) -> int:
    try:
        return len(_client().table("vula_filed_documents").select("id").eq("tenant_id", tenant_id)
                   .eq("status", "pending_project").limit(200).execute().data or [])
    except Exception:
        return 0


def build(tenant_id: str, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or datetime.now(SAST).date()
    try:
        from vula.api.tenants import tenant_profile
        prof = tenant_profile(tenant_id)
    except Exception:
        prof = {}
    return {
        "tenant_id": tenant_id, "day": today.isoformat(),
        "name": prof.get("display_name") or tenant_id,
        "overdue": overdue_invoices(tenant_id, today),
        "spending": spending_jumps(tenant_id, today),
        "low_stock": low_stock(tenant_id) if prof.get("sells_products") else [],
        "unanswered": unanswered(tenant_id, today),
        "profile_gaps": profile_gaps(tenant_id),
        "waiting_docs": waiting_documents(tenant_id),
    }


def render(d: Dict[str, Any]) -> Optional[str]:
    """The WhatsApp text, or None when there's nothing worth saying."""
    parts: List[str] = []
    o = d.get("overdue") or {}
    if o:
        lines = [f"💰 *Owed to you:* {o['count']} overdue invoice{'s' if o['count'] != 1 else ''}, "
                 f"{_r(o['total_cents'])} in total. Biggest:"]
        lines += [f"• {x['who']} — {_r(x['cents'])}, {x['days']} days overdue"
                  + (f" ({x['ref']})" if x.get("ref") else "") for x in o["top"]]
        lines.append("Ask me *which invoices are overdue?* for the full list.")
        parts.append("\n".join(lines))
    if d.get("spending"):
        parts.append("📈 *Spending up this month:*\n" + "\n".join(
            f"• {x['category']}: {_r(x['now_cents'])} vs about {_r(x['usual_cents'])} a month usually (+{x['pct']}%)"
            for x in d["spending"]))
    if d.get("low_stock"):
        parts.append("📦 *Running low:*\n" + "\n".join(
            f"• {x['name']}: {x['qty']} left (reorder at {x['at']})" for x in d["low_stock"]))
    u = d.get("unanswered") or {}
    if u.get("count"):
        line = f"❓ *Questions I had to pass to the team this week:* {u['count']}"
        if u.get("unanswered"):
            line += f", {u['unanswered']} never answered:\n" + "\n".join(f"• “{q}”" for q in u["examples"])
            line += "\nAnswering them teaches me for next time."
        parts.append(line)
    if d.get("waiting_docs"):
        parts.append(f"📄 *{d['waiting_docs']} document(s)* waiting for you to say which project they belong to.")
    if d.get("profile_gaps"):
        parts.append(f"📋 Your business profile has *{d['profile_gaps']} unanswered question(s)* — I say "
                     "\"I'll check\" on those. Send *set up my profile* to finish it.")
    if not parts:
        return None
    return f"Good morning 👋 Your week at {d['name']}:\n\n" + "\n\n".join(parts)


def due(now: datetime) -> bool:
    return now.weekday() == 0 and 8 <= now.hour < 11


async def send_all(now: Optional[datetime] = None) -> Dict[str, Any]:
    from config import settings
    if not settings.owner_advisor_enabled:
        return {"sent": 0, "reason": "owner_advisor_enabled is off"}
    now = now or datetime.now(SAST)
    y, w, _ = now.isocalendar()
    week = f"{y}-W{w:02d}"
    from vula import team_index
    from vula.api.whatsapp import _send_reply
    from vula.api import tenants as _t
    try:
        tenants = [r["tenant_id"] for r in (_client().table("vula_tenant_config")
                   .select("tenant_id").execute().data or [])
                   if r.get("tenant_id") and _t.is_active(r["tenant_id"])]
    except Exception as exc:
        log.warning("advisor: tenant list failed: %s", exc)
        return {"sent": 0}
    sent = 0
    for tid in tenants:
        try:
            text = render(build(tid, now.date()))
            if not text:
                continue
            for m in team_index.active_members(tid):
                if (m.get("role") or "") == "owner" and m.get("whatsapp"):
                    if await _send_reply(m["whatsapp"], text, tid, idem_key=f"advisor:{tid}:{week}:{m['whatsapp']}"):
                        sent += 1
        except Exception as exc:
            log.warning("advisor failed for %s: %s", tid, exc)
    return {"sent": sent, "week": week}
