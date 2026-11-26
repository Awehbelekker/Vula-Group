"""A partner's fee on lessons taught by a coach from another of its schools (26 Nov 2026,
migration 314): a rate the operator can change, and a statement per school per month.

  the rate      `rate_for(tenant)`: the school's own rate if it has one, else the partner's usual
                rate, else the one named in code (`tenants.PARTNERS[id]["pool_fee_bp"]`). In
                hundredths of a percent of the lesson's price. A lesson keeps the rate it was asked
                at (`flow_coach_offers.fee_bp`), so a new rate only reaches coaches asked afterwards.
  the month     `month(partner, month)` is the fee as the lessons stand now (`coach_pool.
                partner_month`: lessons a guest coach taught that the school marked done). `close`
                freezes a finished month into one row per school; `settle` marks a row paid or
                waived. A closed row is what the school owes, whatever is changed afterwards.
  the school    `for_school(tenant)`: its rate, this month so far, its statements and how to pay.

Vula moves no money for this: the school pays the partner the way `pay_note` says, and the
operator marks it. Every figure is integer cents, each lesson's fee worked out and then added.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

MAX_BP = 3000            # a rate above 30% is a typing slip
STATUSES = ("due", "paid", "waived")


def _L():
    from vula.commerce import lessons
    return lessons


def _err(message: str, status: int = 400):
    return _L().LessonError(message, status)


def _db():
    return _L()._db()


def _code_default(pid: str) -> int:
    from vula.api import tenants
    from vula.commerce import coach_pool
    return int((tenants.PARTNERS.get(pid) or {}).get("pool_fee_bp", coach_pool.FEE_BP))


def _known(pid: str) -> str:
    from vula.api import tenants
    if pid not in tenants.PARTNERS:
        raise _err("That partner isn't known.", 404)
    return pid


def _row(pid: str) -> dict:
    try:
        rows = _db().table("flow_partner_settings").select("*").eq("partner_id", pid).limit(1).execute().data or []
        return rows[0] if rows else {}
    except Exception as exc:  # noqa: BLE001 — before the migration the rate is the one in code
        log.debug("partner settings unreadable: %s", exc)
        return {}


def _bp(raw: Any, what: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= MAX_BP:
        raise _err(f"{what} must be between 0% and {MAX_BP // 100}%.")
    return raw


def settings(pid: str) -> dict:
    """The partner's rates as they stand: the usual one, each school on its own, how to pay."""
    from vula.api import tenants
    _known(pid)
    row = _row(pid)
    usual = row.get("pool_fee_bp")
    over = row.get("overrides") if isinstance(row.get("overrides"), dict) else {}
    return {"partner": pid, "name": tenants.PARTNERS[pid]["name"], "code_default_bp": _code_default(pid),
            "pool_fee_bp": int(usual) if usual is not None else _code_default(pid), "set": usual is not None,
            "overrides": [{"tenant_id": t, "school": tenants.display_name(t), "fee_bp": int(bp)}
                          for t, bp in sorted(over.items()) if isinstance(bp, int) and not isinstance(bp, bool)],
            "pay_note": row.get("pay_note") or "", "updated_at": row.get("updated_at"), "updated_by": row.get("updated_by")}


def rate_for(tenant_id: str) -> Optional[int]:
    """The fee a lesson at this school is asked at, or None outside a partner."""
    from vula.commerce import coach_pool
    pid = coach_pool.partner_id(tenant_id)
    if not pid:
        return None
    row = _row(pid)
    over = row.get("overrides") if isinstance(row.get("overrides"), dict) else {}
    own = over.get(tenant_id)
    if isinstance(own, int) and not isinstance(own, bool) and 0 <= own <= MAX_BP:
        return own
    usual = row.get("pool_fee_bp")
    return int(usual) if usual is not None else _code_default(pid)


def save_settings(pid: str, data: Any, by: str = "") -> dict:
    """The operator changes the usual rate, a school's own rate (`overrides`: {tenant_id: bp, or
    null to put the school back on the usual rate}) or how to pay. Only what is sent changes; a
    school must be one of this partner's. Read back."""
    from vula.commerce import coach_pool
    L = _L()
    _known(pid)
    d = data if isinstance(data, dict) else {}
    cur = _row(pid)
    row: Dict[str, Any] = {"partner_id": pid, "pool_fee_bp": cur.get("pool_fee_bp"),
                           "overrides": dict(cur.get("overrides") or {}) if isinstance(cur.get("overrides"), dict) else {},
                           "pay_note": cur.get("pay_note"), "updated_by": by or None, "updated_at": L._now().isoformat()}
    if "pool_fee_bp" in d:
        row["pool_fee_bp"] = None if d["pool_fee_bp"] is None else _bp(d["pool_fee_bp"], "The fee")
    if "overrides" in d:
        if not isinstance(d["overrides"], dict):
            raise _err("Send each school's own rate by its id.")
        for tid, bp in d["overrides"].items():
            tid = str(tid)
            if bp is None:
                row["overrides"].pop(tid, None)
                continue
            if coach_pool.partner_id(tid) != pid:
                raise _err(f"{tid} isn't one of this partner's schools.")
            row["overrides"][tid] = _bp(bp, "A school's own fee")
    if "pay_note" in d:
        note = re.sub(r"[ \t]+", " ", str(d["pay_note"] or "")).strip()
        if len(note) > 400:
            raise _err("Keep how to pay under 400 characters.")
        row["pay_note"] = note or None
    try:
        t = _db().table("flow_partner_settings")
        done = t.update({k: v for k, v in row.items() if k != "partner_id"}).eq("partner_id", pid).execute().data or []
        if not done:
            _db().table("flow_partner_settings").insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("partner settings not saved (%s): %s", pid, exc)
        raise _err("That didn't save. Nothing changed.", 500) from exc
    back = _row(pid)
    if back.get("pool_fee_bp") != row["pool_fee_bp"] or (back.get("overrides") or {}) != row["overrides"] \
            or (back.get("pay_note") or None) != row["pay_note"]:
        raise _err("That didn't save. Nothing changed.", 500)
    return settings(pid)


# ── the month ──────────────────────────────────────────────────────────────────────────────────
def _statements(pid: str, month: str = "", tenant_id: str = "") -> List[dict]:
    try:
        q = _db().table("flow_partner_fee_months").select("*").eq("partner_id", pid)
        if month:
            q = q.eq("month", month)
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        return q.order("month", desc=True).limit(500).execute().data or []
    except Exception as exc:  # noqa: BLE001 — before the migration nothing has been closed
        log.debug("fee statements unreadable: %s", exc)
        return []


def _over(month: str) -> bool:
    L = _L()
    return month < L._now().astimezone(L.SAST).strftime("%Y-%m")


def month(pid: str, month: str) -> dict:
    """A month school by school: the fee as the lessons stand now, and — once the month is closed
    — the frozen statement and whether it was paid. A closed school's figures are the statement's."""
    from vula.api import tenants
    from vula.commerce import coach_pool
    live = coach_pool.partner_month(_known(pid), month)
    closed = {s["tenant_id"]: s for s in _statements(pid, month)}
    rows = []
    for s in live["schools"]:
        st = closed.pop(s["tenant_id"], None)
        rows.append({**s, "live_fee_cents": s["fee_cents"], **({k: st[k] for k in ("lessons", "value_cents", "fee_cents")} if st else {}),
                     "status": st["status"] if st else "open", "note": (st or {}).get("note"),
                     "settled_at": (st or {}).get("settled_at")})
    for tid, st in closed.items():                    # closed, and its lessons have changed since
        rows.append({"tenant_id": tid, "school": tenants.display_name(tid), "lessons": st["lessons"], "value_cents": st["value_cents"],
                     "coach_cents": None, "fee_cents": st["fee_cents"], "live_fee_cents": 0, "status": st["status"],
                     "note": st.get("note"), "settled_at": st.get("settled_at")})
    rows.sort(key=lambda r: r["school"].lower())
    total = lambda *statuses: sum(r["fee_cents"] for r in rows if r["status"] in statuses)  # noqa: E731
    return {"partner": pid, "month": month, "over": _over(month), "schools": rows,
            "total": {"lessons": sum(r["lessons"] for r in rows), "value_cents": sum(r["value_cents"] for r in rows),
                      "fee_cents": sum(r["fee_cents"] for r in rows), "open_cents": total("open"), "due_cents": total("due"),
                      "paid_cents": total("paid"), "waived_cents": total("waived")}}


async def close(pid: str, month: str, by: str = "") -> dict:
    """Freeze a finished month: one statement per school that owes a fee and has none yet. A
    statement already there is never rewritten. Each school is told once."""
    from vula.api import tenants
    L = _L()
    now = month
    _known(pid)
    if not _over(month):
        raise _err("A month can be closed once it is over.", 409)
    live = month_fn(pid, now)
    name = tenants.PARTNERS[pid]["name"]
    note = _row(pid).get("pay_note") or ""
    made = 0
    for s in live["schools"]:
        if s["status"] != "open" or s["fee_cents"] <= 0:
            continue
        try:
            _db().table("flow_partner_fee_months").insert({
                "partner_id": pid, "tenant_id": s["tenant_id"], "month": now, "lessons": s["lessons"],
                "value_cents": s["value_cents"], "fee_cents": s["fee_cents"], "status": "due",
                "closed_by": by or None, "closed_at": L._now().isoformat()}).execute()
        except Exception as exc:  # noqa: BLE001 — another close got there first (the unique key)
            log.info("fee statement for %s %s not written: %s", s["tenant_id"], now, exc)
            continue
        made += 1
        try:
            from vula.integrations.notify import notify_team
            await notify_team(s["tenant_id"], "lesson_request",
                              f"🧾 {name}'s fee for {now}: {L.rand(s['fee_cents'])} on {s['lessons']} "
                              f"{'lesson' if s['lessons'] == 1 else 'lessons'} taught by guest coaches."
                              + (f" {note}" if note else "") + " See Lessons › Coaches.",
                              idem_key=f"partner_fee:{pid}:{s['tenant_id']}:{now}")
        except Exception as exc:  # noqa: BLE001
            log.warning("fee statement not sent to %s: %s", s["tenant_id"], exc)
    return {**month_fn(pid, now), "closed": made}


def settle(pid: str, tenant_id: str, month: str, status: Any, note: Any = "", by: str = "") -> dict:
    """Mark a closed statement paid or waived (or due again). Read back."""
    L = _L()
    _known(pid)
    if status not in STATUSES:
        raise _err("Choose paid, waived or due.")
    rows = _statements(pid, month, tenant_id)
    if not rows:
        raise _err("That month isn't closed for this school yet.", 404)
    words = re.sub(r"\s+", " ", str(note or "")).strip()[:200]
    if status == "waived" and len(words) < 3:
        raise _err("Say why it is waived, in a few words.")
    settled = status != "due"
    _db().table("flow_partner_fee_months").update(
        {"status": status, "note": words or None, "settled_by": (by or None) if settled else None,
         "settled_at": L._now().isoformat() if settled else None}).eq("id", rows[0]["id"]).execute()
    back = _statements(pid, month, tenant_id)
    if not back or back[0]["status"] != status:
        raise _err("That didn't save. Nothing changed.", 500)
    return month_fn(pid, month)


month_fn = month                                      # `close` and `settle` take a `month` of their own


# ── the school's own view ──────────────────────────────────────────────────────────────────────
def for_school(tenant_id: str) -> dict:
    """What a school sees: its rate, this month so far, its statements, and how to pay. Nothing
    about any other school."""
    from vula.api import tenants
    from vula.commerce import coach_pool
    L = _L()
    pid = coach_pool.partner_id(tenant_id)
    if not pid:
        return {"partner": None}
    this = L._now().astimezone(L.SAST).strftime("%Y-%m")
    so_far = L.coach_month(tenant_id, this).get("pool_fee_cents") or 0
    rows = [{"month": s["month"], "lessons": s["lessons"], "value_cents": s["value_cents"], "fee_cents": s["fee_cents"],
             "status": s["status"], "settled_at": s.get("settled_at")} for s in _statements(pid, tenant_id=tenant_id)[:12]]
    return {"partner": tenants.PARTNERS[pid]["name"], "fee_bp": rate_for(tenant_id), "month": this, "so_far_cents": so_far,
            "statements": rows, "due_cents": sum(r["fee_cents"] for r in rows if r["status"] == "due"),
            "pay_note": _row(pid).get("pay_note") or ""}
