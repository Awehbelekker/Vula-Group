"""Each waiter's numbers, and the team's leaderboard (24 Nov 2026, no migration).

Counted from what Tap to Pay and table service already record — nothing is typed in and nothing is
estimated:

  sales     the bill amounts of the paid bills a person served (tips are not sales)
  bills     how many of those there were
  tips      what customers added — shown to the person themselves and to owners and managers,
            NEVER on the board the team shares (what someone earns in tips is theirs to tell)
  calls     table calls they accepted, and how long the table waited for them
  orders    table orders they confirmed

The board is ranked by points, not by sales alone, so being quick for a table counts beside selling:

  1 point per R100 of sales · 5 for a call accepted within two minutes, 2 for a later one ·
  3 per order confirmed

The rule is fixed in code and shown in the app, so nobody is ranked by something they can't see.
Badges name the best in the period at one thing each. A streak is working days in a row with a
paid bill. Test payments never count. Periods are South African days.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")
PERIODS = {"today": 1, "week": 7, "month": 30}
QUICK_SECONDS = 120
POINTS_RULE = ("1 point per R100 of sales, 5 for answering a table within 2 minutes (2 if later), "
               "3 for each order confirmed.")
LOOKBACK_DAYS = 30
MAX_TARGET_CENTS = 99_999_900
logger = logging.getLogger(__name__)


class ScoreError(ValueError):
    pass


def _ts(v) -> datetime:
    from vula.tap.service import _ts as ts
    return ts(v)


@dataclass
class ScoreDesk:
    svc: "object"                                   # TapService

    def tips_on_board(self, tenant_id: str) -> bool:
        """The owner's switch (kb_settings.board_tips, off by default): tips on the board the team shares."""
        return bool((self.svc.repo.get_settings(tenant_id) or {}).get("board_tips"))

    def _since(self, period: str) -> datetime:
        if period not in PERIODS:
            raise ScoreError("Choose today, week or month.")
        today = self.svc.now().astimezone(SAST).date()
        start = today - timedelta(days=PERIODS[period] - 1)
        return datetime.combine(start, time(0, 0), tzinfo=SAST).astimezone(timezone.utc)

    def _tally(self, tenant_id: str, period: str) -> Dict[str, dict]:
        """Every person's numbers for the period, by member id."""
        svc = self.svc
        since = self._since(period)
        back = (svc.now() - timedelta(days=LOOKBACK_DAYS)).isoformat()
        out: Dict[str, dict] = {}

        def row(mid: str) -> dict:
            return out.setdefault(mid, {"sales_cents": 0, "bills": 0, "tips_cents": 0, "calls": 0, "quick_calls": 0,
                                        "wait_seconds": 0, "orders": 0, "days": set()})

        for p in svc.repo.paid_since(tenant_id, back):
            mid = p.get("staff_id")
            if not mid or p.get("is_test"):
                continue
            r = row(mid)
            when = _ts(p["paid_at"])
            r["days"].add(when.astimezone(SAST).date())
            if when >= since:
                r["sales_cents"] += int(p["bill_cents"])
                r["tips_cents"] += int(p["tip_cents"])
                r["bills"] += 1
        for c in svc.repo.rows_since("kb_table_calls", tenant_id, since.isoformat()):
            mid, at = c.get("accepted_by"), c.get("accepted_at")
            if not mid or not at:
                continue
            waited = max(0, int((_ts(at) - _ts(c["created_at"])).total_seconds()))
            r = row(mid)
            r["calls"] += 1
            r["wait_seconds"] += waited
            r["quick_calls"] += waited <= QUICK_SECONDS
        for o in svc.repo.rows_since("kb_table_orders", tenant_id, since.isoformat()):
            if o.get("status") == "confirmed" and o.get("decided_by"):
                row(o["decided_by"])["orders"] += 1
        return out

    @staticmethod
    def points(r: dict) -> int:
        return (r["sales_cents"] // 10_000 + 5 * r["quick_calls"] + 2 * (r["calls"] - r["quick_calls"])
                + 3 * r["orders"])

    def _streak(self, days: set) -> int:
        """Days in a row with a paid bill, ending today (or yesterday, before today's first)."""
        today = self.svc.now().astimezone(SAST).date()
        d = today if today in days else today - timedelta(days=1)
        n = 0
        while d in days:
            n += 1
            d -= timedelta(days=1)
        return n

    def board(self, tenant_id: str, period: str = "today", *, with_tips: bool = False) -> List[dict]:
        """The ranked list. `with_tips` is for owners and managers only — the shared board has none."""
        svc = self.svc
        tally = self._tally(tenant_id, period)
        names = {m["id"]: (m.get("name") or "").split(" ")[0] or "Staff" for m in svc.repo.team_members(tenant_id)}
        rows = []
        for mid, r in tally.items():
            if mid not in names or not (r["bills"] or r["calls"] or r["orders"]):
                continue
            row = {"member_id": mid, "name": names[mid], "points": self.points(r), "sales_cents": r["sales_cents"],
                   "bills": r["bills"], "calls": r["calls"], "orders": r["orders"],
                   "avg_wait_seconds": r["wait_seconds"] // r["calls"] if r["calls"] else None,
                   "streak_days": self._streak(r["days"]), "badges": []}
            if with_tips:
                row["tips_cents"] = r["tips_cents"]
            rows.append(row)
        rows.sort(key=lambda x: (-x["points"], -x["sales_cents"], x["name"]))
        for n, row in enumerate(rows, 1):
            row["rank"] = n
        self._badges(rows)
        return rows

    @staticmethod
    def _badges(rows: List[dict]) -> None:
        """One badge per thing, to whoever is alone at the top of it (a tie gets no badge)."""
        def best(key, value, label, need=1, low=False):
            have = [r for r in rows if value(r) is not None and key(r) >= need]
            if not have:
                return
            top = min(have, key=value) if low else max(have, key=value)
            if sum(1 for r in have if value(r) == value(top)) == 1:
                top["badges"].append(label)
        best(lambda r: r["sales_cents"], lambda r: r["sales_cents"], "Top seller")
        best(lambda r: r["bills"], lambda r: r["bills"], "Most tables")
        best(lambda r: r["calls"], lambda r: r["avg_wait_seconds"], "Fastest to a table", need=3, low=True)

    def mine(self, tenant_id: str, member_id: str, period: str = "today") -> dict:
        """One person's own numbers, tips included, and where they stand."""
        tally = self._tally(tenant_id, period)
        r = tally.get(member_id) or {"sales_cents": 0, "bills": 0, "tips_cents": 0, "calls": 0, "quick_calls": 0,
                                     "wait_seconds": 0, "orders": 0, "days": set()}
        board = self.board(tenant_id, period)
        me = next((b for b in board if b["member_id"] == member_id), None)
        return {"period": period, "sales_cents": r["sales_cents"], "bills": r["bills"], "tips_cents": r["tips_cents"],
                "calls": r["calls"], "orders": r["orders"],
                "avg_wait_seconds": r["wait_seconds"] // r["calls"] if r["calls"] else None,
                "points": self.points(r), "rank": me["rank"] if me else None, "of": len(board),
                "streak_days": self._streak(r["days"]), "badges": me["badges"] if me else []}

    def target(self, tenant_id: str, member_id: str) -> Optional[dict]:
        """The person's own target, or None. Never raises: before migration 307 (or with the
        database away) there is simply none, and the app keeps the one on the phone."""
        try:
            t = self.svc.repo.get_target(tenant_id, member_id)
        except Exception:  # noqa: BLE001
            logger.debug("target read failed (tenant=%s)", tenant_id, exc_info=True)
            return None
        return {"period": t["period"], "cents": int(t["cents"])} if t and t.get("cents") else None

    def set_target(self, tenant_id: str, member_id: str, period: Any, cents: Any) -> Optional[dict]:
        """Save (or with no amount, remove) the person's own target, and read it back."""
        if cents in (None, 0):
            self.svc.repo.set_target(tenant_id, member_id, None, None)
            return None
        if period not in ("day", "week"):
            raise ScoreError("Choose a target for each day or each week.")
        if isinstance(cents, bool) or not isinstance(cents, int) or not 100 <= cents <= MAX_TARGET_CENTS:
            raise ScoreError("Type a target between R1 and R999,999.")
        self.svc.repo.set_target(tenant_id, member_id, period, cents)
        saved = self.svc.repo.get_target(tenant_id, member_id)
        if not saved or int(saved["cents"]) != cents or saved["period"] != period:
            raise ScoreError("Your target couldn't be saved. Try again.")
        return {"period": period, "cents": cents}

    def income(self, tenant_id: str, member_id: str, days: int = 30) -> dict:
        """What one person has earned, day by day (South African days, oldest first): their share
        from the ledger — bills and tips, less any fee they carry — beside the sales and tips those
        days. Only ever that person's own."""
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 60:
            raise ScoreError("Choose between 1 and 60 days.")
        svc = self.svc
        today = svc.now().astimezone(SAST).date()
        start = today - timedelta(days=days - 1)
        since = datetime.combine(start, time(0, 0), tzinfo=SAST).astimezone(timezone.utc).isoformat()
        by_day = {start + timedelta(days=n): {"earned_cents": 0, "sales_cents": 0, "tips_cents": 0, "bills": 0}
                  for n in range(days)}
        for line in svc.repo.ledger_for(tenant_id, member_id, since):
            d = _ts(line["created_at"]).astimezone(SAST).date()
            if d in by_day:
                by_day[d]["earned_cents"] += int(line["cents"])
        for p in svc.repo.paid_since(tenant_id, since):
            if p.get("staff_id") != member_id or p.get("is_test"):
                continue
            d = _ts(p["paid_at"]).astimezone(SAST).date()
            if d in by_day:
                by_day[d]["sales_cents"] += int(p["bill_cents"])
                by_day[d]["tips_cents"] += int(p["tip_cents"])
                by_day[d]["bills"] += 1

        def total(n: int) -> dict:
            rows = [v for d, v in by_day.items() if d > today - timedelta(days=n)]
            return {k: sum(r[k] for r in rows) for k in ("earned_cents", "sales_cents", "tips_cents", "bills")}
        monday = today - timedelta(days=today.weekday())
        this_week = [v for d, v in by_day.items() if d >= monday]
        return {"days": [{"date": d.isoformat(), **v} for d, v in sorted(by_day.items())],
                "today": total(1), "last_7": total(min(7, days)), "last_30": total(min(30, days)),
                "this_week": {k: sum(r[k] for r in this_week) for k in ("earned_cents", "sales_cents", "tips_cents", "bills")},
                "target": self.target(tenant_id, member_id)}
