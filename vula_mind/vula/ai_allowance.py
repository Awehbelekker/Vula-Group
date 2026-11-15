"""A business's monthly AI allowance and the top-ups it buys (14 Nov 2026, migration 270).

Ian's decisions: Vula runs the AI and bills each business. Every plan includes a month of cloud AI
— Starter R100, Growth R300, Business R750 — and a business can buy top-ups of R100, R250 or R500
(at cost plus 30%, by EFT; Master marks them paid, as plans are paid today).

  * One unit everywhere: rand at Vula's price = the cloud cost in USD (vula_ai_usage, metered on
    every call) × settings.usd_zar_rate × settings.ai_markup, in integer cents. Use on Vula's own
    model costs nothing and never counts.
  * The allowance resets each calendar month; a paid top-up lasts until it's used up. The balance
    is worked out from the monthly totals each time — nothing is counted down, so it can always be
    recomputed and checked.
  * Used up → only the extras pause: Ask anything and research (llm_router.CAP_HARD_TASKS) stay on
    Vula's own model, or pause if it's down. Customer replies, document reading and the business
    assistant never stop; their cloud use is still recorded and counted.
  * The owner is told at 80% and when it's used up, once each a month (notify, event ai_allowance).
  * Fail-open: a read error never pauses anything.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PLAN_ALLOWANCE_CENTS = {"starter": 10_000, "growth": 30_000, "business": 75_000}
TOPUP_PACKS_CENTS = (10_000, 25_000, 50_000)
CREDITS = "vula_ai_credits"
CACHE_S = 60.0
_SAST = timezone(timedelta(hours=2))
_cache: Dict[str, tuple] = {}
_lock = threading.Lock()


class AllowanceError(ValueError):
    """A request that can't be done; the message says why, for the person."""


def _client():
    from vula.commerce import service
    return service._client()


def month_of(when: Optional[datetime] = None) -> str:
    """'2026-11' for the calendar month in South Africa."""
    return (when or datetime.now(timezone.utc)).astimezone(_SAST).strftime("%Y-%m")


def to_cents(usd: float) -> int:
    """USD cost → rand cents at Vula's price."""
    from config import settings
    return int(round(float(usd or 0) * float(settings.usd_zar_rate) * float(settings.ai_markup) * 100))


def rand(cents: int) -> str:
    """12345 → 'R123.45' (R123 when whole)."""
    whole, part = divmod(abs(int(cents)), 100)
    s = f"R{whole:,}".replace(",", " ") + (f".{part:02d}" if part else "")
    return f"-{s}" if cents < 0 else s


def _config(tenant_id: str) -> Dict[str, Any]:
    try:
        from vula.api.tenants import get_config
        return get_config(tenant_id) or {}
    except Exception:  # noqa: BLE001
        return {}


def allowance_cents(tenant_id: str) -> int:
    """This business's monthly allowance: Master's override, else its plan's (trial = Starter)."""
    cfg = _config(tenant_id)
    if cfg.get("ai_allowance_cents") is not None:
        return max(0, int(cfg["ai_allowance_cents"]))
    plan = str(cfg.get("plan") or "starter").lower()
    return PLAN_ALLOWANCE_CENTS.get(plan, PLAN_ALLOWANCE_CENTS["starter"])


def monthly_used(tenant_id: str, since_month: str) -> Dict[str, int]:
    """{'2026-11': cents, …} from since_month on (the usage table's own dates)."""
    rows = (_client().table("vula_ai_usage").select("day,est_cost_usd").eq("tenant_id", tenant_id)
            .gte("day", f"{since_month}-01").execute().data or [])
    usd: Dict[str, float] = {}
    for r in rows:
        m = str(r.get("day") or "")[:7]
        if m:
            usd[m] = usd.get(m, 0.0) + float(r.get("est_cost_usd") or 0)
    return {m: to_cents(v) for m, v in usd.items()}


def daily_used(tenant_id: str, month: str) -> List[Dict[str, Any]]:
    """This month's cloud use by day, for the chart: [{day, cents}]."""
    rows = (_client().table("vula_ai_usage").select("day,est_cost_usd").eq("tenant_id", tenant_id)
            .gte("day", f"{month}-01").execute().data or [])
    by: Dict[str, float] = {}
    for r in rows:
        d = str(r.get("day") or "")[:10]
        if d.startswith(month):
            by[d] = by.get(d, 0.0) + float(r.get("est_cost_usd") or 0)
    return [{"day": d, "cents": to_cents(v)} for d, v in sorted(by.items())]


def credits(tenant_id: str, statuses: tuple = ("paid",)) -> List[Dict[str, Any]]:
    return (_client().table(CREDITS).select("*").eq("tenant_id", tenant_id)
            .in_("status", list(statuses)).order("requested_at").execute().data or [])


def _paid_month(c: Dict[str, Any]) -> str:
    try:
        return month_of(datetime.fromisoformat(str(c.get("paid_at") or c.get("requested_at")).replace("Z", "+00:00")))
    except ValueError:
        return month_of()


def compute(allowance: int, used_by_month: Dict[str, int], paid: List[Dict[str, Any]],
            current: str) -> Dict[str, Any]:
    """The balance, from the totals: each month's use beyond the allowance draws on the top-ups
    paid by then, oldest first; what's left carries on."""
    months = sorted(set(used_by_month) | {_paid_month(c) for c in paid} | {current})
    pool = 0
    for m in months:
        if m > current:
            break
        pool += sum(int(c.get("amount_cents") or 0) for c in paid if _paid_month(c) == m)
        over = max(0, used_by_month.get(m, 0) - allowance)
        if m < current:
            pool = max(0, pool - over)
    used = used_by_month.get(current, 0)
    over_now = max(0, used - allowance)
    topups_left = max(0, pool - over_now)
    budget = allowance + pool
    return {"month": current, "allowance": allowance, "used": used, "topups_left": topups_left,
            "remaining": max(0, budget - used), "pct": round(100 * used / budget) if budget else 100,
            "paused": used >= budget}


def status(tenant_id: str, fresh: bool = False) -> Dict[str, Any]:
    """This month: allowance, used, top-ups left, remaining, % used, paused — plus open requests."""
    now = time.time()
    if not fresh:
        with _lock:
            hit = _cache.get(tenant_id)
            if hit and now - hit[0] < CACHE_S:
                return dict(hit[1])
    current = month_of()
    paid = credits(tenant_id)
    since = min([current] + [_paid_month(c) for c in paid])
    out = compute(allowance_cents(tenant_id), monthly_used(tenant_id, since), paid, current)
    out["requested"] = [{"id": c["id"], "amount": int(c["amount_cents"]), "requested_at": c.get("requested_at")}
                        for c in credits(tenant_id, ("requested",))]
    with _lock:
        _cache[tenant_id] = (now, dict(out))
    return out


def forget(tenant_id: str) -> None:
    with _lock:
        _cache.pop(tenant_id, None)


def extras_paused(tenant_id: Optional[str]) -> bool:
    """Ask anything and research are paused for this business (allowance and top-ups used).
    Never True on a read error."""
    if not tenant_id or tenant_id.startswith("_"):
        return False
    try:
        return bool(status(tenant_id)["paused"])
    except Exception as exc:  # noqa: BLE001 — fail open
        logger.debug("ai allowance unreadable for %s: %s", tenant_id, exc)
        return False


# ── telling the owner ───────────────────────────────────────────────────────────────────────

def notice(st: Dict[str, Any]) -> Optional[tuple]:
    """(level, message) to send now, or None. 100 when used up, 80 from 80%."""
    if st["paused"]:
        return 100, (f"Your AI allowance for this month is used up ({rand(st['used'])} used). "
                     "Ask anything and research now use Vula's own AI only until next month or a top-up "
                     "— your customers aren't affected. An owner can add a top-up in Settings › AI usage.")
    if st["pct"] >= 80:
        return 80, (f"You've used {st['pct']}% of this month's AI allowance ({rand(st['used'])}). "
                    "Nothing changes for your customers. To keep Ask anything and research on the fuller "
                    "AI for the rest of the month, an owner can add a top-up in Settings › AI usage.")
    return None


async def after_spend(tenant_id: str) -> None:
    """After a cloud call is metered: tell the owner at 80% and when it's used up, once each a
    month (the notify idem key). Best-effort."""
    if not tenant_id or tenant_id.startswith("_"):
        return
    try:
        forget(tenant_id)
        st = status(tenant_id, fresh=True)
        n = notice(st)
        if not n:
            return
        from vula.integrations.notify import notify_team
        await notify_team(tenant_id, "ai_allowance", n[1], idem_key=f"ai{n[0]}:{tenant_id}:{st['month']}")
    except Exception as exc:  # noqa: BLE001
        logger.debug("ai allowance notice skipped for %s: %s", tenant_id, exc)


# ── top-ups ─────────────────────────────────────────────────────────────────────────────────

async def request_topup(tenant_id: str, person_key: str, allowed: bool, pack_cents: int) -> Dict[str, Any]:
    """An owner or manager (`allowed`, decided by the route from the login's scope) asks for a
    top-up. It counts once Master marks it paid."""
    if not allowed:
        raise AllowanceError("Only an owner or manager can add a top-up.")
    if int(pack_cents) not in TOPUP_PACKS_CENTS:
        raise AllowanceError("Top-ups are R100, R250 or R500.")
    open_ = [c for c in credits(tenant_id, ("requested",)) if int(c["amount_cents"]) == int(pack_cents)]
    if open_:
        return {**open_[0], "already": True}
    row = {"tenant_id": tenant_id, "kind": "topup", "amount_cents": int(pack_cents), "status": "requested",
           "requested_by": person_key or None, "requested_at": datetime.now(timezone.utc).isoformat()}
    got = _client().table(CREDITS).insert(row).execute().data or []
    if not got or not got[0].get("id"):
        raise AllowanceError("That request couldn't be saved — please try again.")
    forget(tenant_id)
    try:
        from vula.api.tenants import display_name
        from vula.integrations.platform_support import alert_operator
        await alert_operator(f"AI top-up requested: {display_name(tenant_id)} ({tenant_id}) — {rand(pack_cents)}. "
                             f"Invoice it, then mark it paid in Master (ref {got[0]['id'][:8]}).")
    except Exception as exc:  # noqa: BLE001
        logger.debug("top-up alert skipped: %s", exc)
    return got[0]


def _set(credit_id: str, patch: Dict[str, Any], want_status: str) -> Optional[Dict[str, Any]]:
    _client().table(CREDITS).update(patch).eq("id", credit_id).execute()
    rows = _client().table(CREDITS).select("*").eq("id", credit_id).limit(1).execute().data or []
    row = rows[0] if rows else None
    if row:
        forget(row["tenant_id"])
    return row if row and row.get("status") == want_status else None


def mark_paid(credit_id: str, by: str, reference: str = "") -> Optional[Dict[str, Any]]:
    """Master: the invoice was paid. Read back; None when it isn't a requested top-up."""
    rows = _client().table(CREDITS).select("*").eq("id", credit_id).limit(1).execute().data or []
    if not rows or rows[0].get("status") != "requested":
        return None
    return _set(credit_id, {"status": "paid", "paid_at": datetime.now(timezone.utc).isoformat(),
                            "paid_by": by, "reference": (reference or "")[:120] or None}, "paid")


def cancel(credit_id: str) -> Optional[Dict[str, Any]]:
    rows = _client().table(CREDITS).select("*").eq("id", credit_id).limit(1).execute().data or []
    if not rows or rows[0].get("status") != "requested":
        return None
    return _set(credit_id, {"status": "cancelled"}, "cancelled")


def grant(tenant_id: str, amount_cents: int, by: str, note: str = "") -> Dict[str, Any]:
    """Master adds credit by hand (paid at once). Read back."""
    amount = int(amount_cents)
    if amount <= 0 or amount > 10_000_000:
        raise AllowanceError("Give an amount in rand, more than 0.")
    now = datetime.now(timezone.utc).isoformat()
    got = (_client().table(CREDITS).insert({"tenant_id": tenant_id, "kind": "grant", "amount_cents": amount,
                                            "status": "paid", "requested_by": by, "requested_at": now,
                                            "paid_at": now, "paid_by": by, "note": (note or "")[:200] or None})
           .execute().data or [])
    if not got or not got[0].get("id"):
        raise AllowanceError("The credit couldn't be saved.")
    rows = _client().table(CREDITS).select("*").eq("id", got[0]["id"]).limit(1).execute().data or []
    if not rows or rows[0].get("status") != "paid":
        raise AllowanceError("The credit couldn't be confirmed.")
    forget(tenant_id)
    return rows[0]


def waiting() -> List[Dict[str, Any]]:
    """Master: every business's requested top-ups, oldest first."""
    return (_client().table(CREDITS).select("*").eq("status", "requested").order("requested_at")
            .execute().data or [])
