"""Shifts and availability (24 Nov 2026, migration 301).

A manager plans who works when and publishes the week. Each person then confirms, says they can't
make it, or reports sick — in the staff app or by writing to the business's WhatsApp number — and
the owners and managers are told at once. Staff say when they can work (weekly times, and days
off), and the planner shows it; a shift outside someone's times is allowed but flagged, because the
manager may know better. Two shifts for one person can never overlap.

A shift with nobody on it is open: staff see it once published, and the first to take it gets it
(one conditional write, so two people tapping at once get one winner).

Staff only ever see published shifts. Times are South African time. This is a roster, not payroll:
hours are shown, pay is not worked out.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SAST = ZoneInfo("Africa/Johannesburg")
SHIFTS, AVAIL = "kb_shifts", "kb_availability"
LIVE = ("published", "confirmed")
# The approved template that carries a roster message to a team member who hasn't written in the
# last 24 hours (vula/commerce/wa_templates.py submits it with the standard set). One per WhatsApp
# account, shared by name: first name, the business, the message on one line.
SHIFT_TEMPLATE = "vula_staff_shifts"
SHIFT_TEMPLATE_BODY = ("Hi {{1}}, a message from {{2}} about your shifts: {{3}} "
                       "Reply MY SHIFTS to see them, CONFIRM to confirm, or tell us if you can't make it.")
SHIFT_TEMPLATE_EXAMPLE = ["Thandi", "The Yard", "Your shifts: Fri 27 Nov 17:00 to 23:00 (Floor). Please confirm."]
TEMPLATE_ROOM = 190                      # send_template cuts a value at 200 characters
STAFF_SEES = ("published", "confirmed", "declined", "sick")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_SICK_RE = re.compile(r"^\s*(i'?m\s+|i\s+am\s+|am\s+)?(off\s+)?sick\b|\bcalling\s+in\s+sick\b|\bek\s+is\s+siek\b", re.I)
_CANT_RE = re.compile(r"\b(can'?t|cannot|won'?t\s+be\s+able\s+to)\s+(make|do|work)\s+(it|my\s+shift|the\s+shift|tonight|today|tomorrow)\b", re.I)
_MINE_RE = re.compile(r"^\s*(my\s+shifts?|shifts?|roster|when\s+(do|am)\s+i\s+work(ing)?\??)\s*$", re.I)
_CONFIRM_RE = re.compile(r"^\s*confirm(\s+(my\s+)?shifts?)?\s*$", re.I)


class ShiftError(ValueError):
    """Shown to the person as is."""


def _ts(v) -> datetime:
    from vula.tap.service import _ts as ts
    return ts(v)


def _text(raw: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()[:limit].strip()


def when_text(starts_at: Any, ends_at: Any) -> str:
    """Sat 6 Dec, 17:00–23:00 — South African time."""
    s, e = _ts(starts_at).astimezone(SAST), _ts(ends_at).astimezone(SAST)
    return f"{s.strftime('%a')} {s.day} {s.strftime('%b')}, {s.strftime('%H:%M')}–{e.strftime('%H:%M')}"


@dataclass
class ShiftDesk:
    svc: "object"                                   # TapService

    # ── people ───────────────────────────────────────────────────────────────────────────────
    def _members(self, tenant_id: str) -> Dict[str, dict]:
        return {m["id"]: m for m in self.svc.repo.team_members(tenant_id)}

    def _name(self, members: dict, member_id: Optional[str]) -> str:
        return ((members.get(member_id) or {}).get("name") or "").split(" ")[0] if member_id else ""

    def member_for_phone(self, tenant_id: str, phone: str) -> Optional[dict]:
        digits = re.sub(r"\D", "", phone or "")[-9:]
        if len(digits) < 9:
            return None
        return next((m for m in self.svc.repo.team_members(tenant_id)
                     if re.sub(r"\D", "", m.get("whatsapp") or "")[-9:] == digits), None)

    def on_now(self, tenant_id: str) -> List[str]:
        """Who is on a published or confirmed shift right now (table calls go to them)."""
        now = self.svc.now()
        rows = self.svc.repo.shifts_between(tenant_id, (now - timedelta(hours=16)).isoformat(),
                                            (now + timedelta(seconds=1)).isoformat())
        out: List[str] = []
        for s in rows:
            if s.get("member_id") and s["status"] in LIVE and _ts(s["ends_at"]) > now and s["member_id"] not in out:
                out.append(s["member_id"])
        return out

    async def _whatsapp(self, tenant_id: str, phone: str, name: str, title: str, body: str) -> None:
        """A WhatsApp message to one of the team. Outside WhatsApp's 24-hour window a plain message
        is refused, so the approved roster template carries the same words on one line."""
        svc = self.svc
        if await svc._say_ok(tenant_id, phone, f"{title}: {body}"):
            return
        line = re.sub(r"\s+", " ", f"{title}: {body}").strip()[:TEMPLATE_ROOM]
        await svc.messenger.template(tenant_id, phone, SHIFT_TEMPLATE,
                                     [name or "there", svc.repo.merchant_name(tenant_id) or "your workplace", line])

    async def _tell(self, tenant_id: str, member_id: Optional[str], title: str, body: str, ref: str) -> None:
        """One person (push + WhatsApp), or with no member the owners and managers."""
        svc = self.svc
        members = self._members(tenant_id)
        ids = [member_id] if member_id else [m["id"] for m in members.values() if m.get("role") in ("owner", "manager")]
        for mid in ids:
            await svc._push(tenant_id, mid, title, body, tag=ref)
            phone = (members.get(mid) or {}).get("whatsapp")
            if phone:
                try:
                    await self._whatsapp(tenant_id, phone, self._name(members, mid), title, body)
                except Exception:  # noqa: BLE001 — a message that doesn't send never loses the change
                    logger.exception("shift message failed (tenant=%s)", tenant_id)

    # ── availability ─────────────────────────────────────────────────────────────────────────
    def availability(self, tenant_id: str, member_id: str) -> dict:
        rows = self.svc.repo.availability(tenant_id, member_id)
        weekly = sorted(({"id": r["id"], "weekday": r["weekday"], "start": r["start_time"], "end": r["end_time"]}
                         for r in rows if r["kind"] == "weekly"), key=lambda r: (r["weekday"], r["start"]))
        today = self.svc.now().astimezone(SAST).date().isoformat()
        off = sorted(({"id": r["id"], "from": str(r["from_date"]), "to": str(r["to_date"]), "note": r.get("note") or ""}
                      for r in rows if r["kind"] == "off" and str(r["to_date"]) >= today), key=lambda r: r["from"])
        return {"weekly": weekly, "off": off}

    def add_weekly(self, tenant_id: str, member_id: str, weekday: Any, start: Any, end: Any) -> dict:
        if isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6:
            raise ShiftError("Choose a day of the week.")
        start, end = str(start or ""), str(end or "")
        if not _TIME_RE.match(start) or not _TIME_RE.match(end) or end <= start:
            raise ShiftError("Give a start and an end time, the end after the start (like 17:00 to 23:00).")
        have = self.svc.repo.availability(tenant_id, member_id)
        if any(r["kind"] == "weekly" and r["weekday"] == weekday and r["start_time"] < end and r["end_time"] > start for r in have):
            raise ShiftError(f"You already have times on {DAY_NAMES[weekday]} that overlap those.")
        if sum(1 for r in have if r["kind"] == "weekly") >= 28:
            raise ShiftError("That's a lot of times. Remove one first.")
        self.svc.repo.add_row(AVAIL, {"tenant_id": tenant_id, "member_id": member_id, "kind": "weekly",
                                      "weekday": weekday, "start_time": start, "end_time": end})
        return self.availability(tenant_id, member_id)

    def add_time_off(self, tenant_id: str, member_id: str, from_date: Any, to_date: Any, note: Any = "") -> dict:
        try:
            d1, d2 = date.fromisoformat(str(from_date)), date.fromisoformat(str(to_date or from_date))
        except ValueError as exc:
            raise ShiftError("Choose the first and last day you're away.") from exc
        today = self.svc.now().astimezone(SAST).date()
        if d2 < d1 or d2 < today or (d2 - d1).days > 60:
            raise ShiftError("The days away must be coming up, the last day after the first, and no more than 60 days.")
        self.svc.repo.add_row(AVAIL, {"tenant_id": tenant_id, "member_id": member_id, "kind": "off",
                                      "from_date": d1.isoformat(), "to_date": d2.isoformat(),
                                      "note": _text(note, 80) or None})
        return self.availability(tenant_id, member_id)

    def remove_availability(self, tenant_id: str, member_id: str, row_id: str) -> dict:
        """Only the person's own row (the delete is matched on their id too)."""
        if not self.svc.repo.delete_row(AVAIL, tenant_id, row_id, member_id=member_id):
            raise ShiftError("That isn't one of your times.")
        return self.availability(tenant_id, member_id)

    def _fits(self, avail_rows: List[dict], member_id: str, start: datetime, end: datetime) -> str:
        """'' when the shift sits inside what the person said they can work; else why not."""
        mine = [r for r in avail_rows if r["member_id"] == member_id]
        s, e = start.astimezone(SAST), end.astimezone(SAST)
        for r in mine:
            if r["kind"] == "off" and str(r["from_date"]) <= s.date().isoformat() <= str(r["to_date"]):
                return "is away that day" + (f" ({r['note']})" if r.get("note") else "")
        weekly = [r for r in mine if r["kind"] == "weekly"]
        if not weekly:
            return ""                                   # said nothing: nothing to go against
        same_day = s.date() == e.date()
        for r in weekly:
            if r["weekday"] == s.weekday() and r["start_time"] <= s.strftime("%H:%M") and (
                    not same_day or e.strftime("%H:%M") <= r["end_time"]):
                return ""
        return f"hasn't said they can work then ({DAY_NAMES[s.weekday()]} {s.strftime('%H:%M')}–{e.strftime('%H:%M')})"

    # ── planning (owners and managers) ───────────────────────────────────────────────────────
    def _week(self, day: Any) -> tuple:
        try:
            d = date.fromisoformat(str(day)) if day else self.svc.now().astimezone(SAST).date()
        except ValueError as exc:
            raise ShiftError("Choose a week.") from exc
        monday = d - timedelta(days=d.weekday())
        lo = datetime.combine(monday, time(0, 0), tzinfo=SAST)
        return monday, lo.astimezone(timezone.utc), (lo + timedelta(days=7)).astimezone(timezone.utc)

    def _view(self, s: dict, members: dict, avail: Optional[List[dict]] = None) -> dict:
        out = {"id": s["id"], "member_id": s.get("member_id"), "name": self._name(members, s.get("member_id")) or None,
               "starts_at": s["starts_at"], "ends_at": s["ends_at"], "when": when_text(s["starts_at"], s["ends_at"]),
               "date": _ts(s["starts_at"]).astimezone(SAST).date().isoformat(),
               "hours": round((_ts(s["ends_at"]) - _ts(s["starts_at"])).total_seconds() / 3600, 2),
               "section": s.get("section") or "", "note": s.get("note") or "", "status": s["status"],
               "reason": s.get("reason") or "", "open": not s.get("member_id")}
        if avail is not None and s.get("member_id") and s["status"] in ("draft",) + LIVE:
            out["warning"] = self._fits(avail, s["member_id"], _ts(s["starts_at"]), _ts(s["ends_at"]))
        return out

    def week(self, tenant_id: str, day: Any = None) -> dict:
        """The planner's week: every shift, the team with their hours and what they said they can work."""
        monday, lo, hi = self._week(day)
        members = self._members(tenant_id)
        avail = self.svc.repo.availability(tenant_id)
        rows = [s for s in self.svc.repo.shifts_between(tenant_id, lo.isoformat(), hi.isoformat()) if s["status"] != "cancelled"]
        shifts = sorted((self._view(s, members, avail) for s in rows), key=lambda s: (s["starts_at"], s["name"] or ""))
        team = []
        for m in members.values():
            mine = [s for s in shifts if s["member_id"] == m["id"] and s["status"] in ("draft",) + LIVE]
            team.append({"id": m["id"], "name": m.get("name") or "", "role": m.get("role"),
                         "hours": round(sum(s["hours"] for s in mine), 2), "shifts": len(mine),
                         "availability": self.availability(tenant_id, m["id"])})
        return {"week_start": monday.isoformat(), "days": [(monday + timedelta(days=n)).isoformat() for n in range(7)],
                "shifts": shifts, "team": sorted(team, key=lambda t: t["name"].lower()),
                "drafts": sum(1 for s in shifts if s["status"] == "draft")}

    def _clean(self, tenant_id: str, data: dict, current: Optional[dict] = None) -> dict:
        cur = current or {}
        members = self._members(tenant_id)
        mid = data["member_id"] if "member_id" in data else cur.get("member_id")
        mid = mid or None
        if mid and mid not in members:
            raise ShiftError("Unknown team member.")
        def read(key: str) -> datetime:
            # A time typed in the planner carries no zone: it is South African time. (What is
            # already stored has its zone.)
            raw = data.get(key) or cur.get(key)
            d = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=SAST)
        try:
            start, end = read("starts_at"), read("ends_at")
        except Exception as exc:  # noqa: BLE001
            raise ShiftError("Give the shift a start and an end time.") from exc
        if end <= start or end - start > timedelta(hours=16):
            raise ShiftError("A shift ends after it starts and lasts no more than 16 hours.")
        if mid:
            lo, hi = (start - timedelta(hours=16)).isoformat(), (end + timedelta(hours=1)).isoformat()
            for o in self.svc.repo.shifts_between(tenant_id, lo, hi):
                if (o["id"] != cur.get("id") and o.get("member_id") == mid and o["status"] in ("draft",) + LIVE
                        and _ts(o["starts_at"]) < end and _ts(o["ends_at"]) > start):
                    raise ShiftError(f"{self._name(members, mid)} already has a shift then ({when_text(o['starts_at'], o['ends_at'])}).")
        return {"member_id": mid, "starts_at": start.astimezone(timezone.utc).isoformat(),
                "ends_at": end.astimezone(timezone.utc).isoformat(),
                "section": _text(data.get("section", cur.get("section")), 40) or None,
                "note": _text(data.get("note", cur.get("note")), 120) or None}

    def create(self, tenant_id: str, data: dict, by: str) -> dict:
        row = self.svc.repo.add_row(SHIFTS, {"tenant_id": tenant_id, "status": "draft", "created_by": by,
                                             **self._clean(tenant_id, data or {})})
        return self._view(row, self._members(tenant_id), self.svc.repo.availability(tenant_id))

    def copy_week(self, tenant_id: str, day: Any, by: str) -> dict:
        """Last week's roster as this week's drafts: the same people at the same times, seven days
        on. Nothing is published and nobody is told. A shift is left out when its person has left
        the team or already has a shift then (so copying twice adds nothing); cancelled ones are
        never copied; one nobody held comes across as an open shift."""
        monday, lo, hi = self._week(day)
        week = timedelta(days=7)
        members = self._members(tenant_id)
        source = [s for s in self.svc.repo.shifts_between(tenant_id, (lo - week).isoformat(), (hi - week).isoformat())
                  if s["status"] != "cancelled"]
        there = [s for s in self.svc.repo.shifts_between(tenant_id, (lo - timedelta(hours=16)).isoformat(), hi.isoformat())
                 if s["status"] != "cancelled"]
        copied, left, clash = 0, 0, 0
        for s in sorted(source, key=lambda r: r["starts_at"]):
            mid = s.get("member_id") or None
            if mid and mid not in members:
                left += 1
                continue
            start, end = _ts(s["starts_at"]) + week, _ts(s["ends_at"]) + week
            if any((o.get("member_id") or None) == mid and _ts(o["starts_at"]) < end and _ts(o["ends_at"]) > start
                   and (mid or (_ts(o["starts_at"]) == start and _ts(o["ends_at"]) == end)) for o in there):
                clash += 1
                continue
            row = self.svc.repo.add_row(SHIFTS, {
                "tenant_id": tenant_id, "status": "draft", "created_by": by, "member_id": mid,
                "starts_at": start.astimezone(timezone.utc).isoformat(), "ends_at": end.astimezone(timezone.utc).isoformat(),
                "section": s.get("section") or None, "note": s.get("note") or None})
            there.append(row)
            copied += 1
        return {"copied": copied, "already_there": clash, "left_the_team": left, "last_week": len(source),
                "week_start": monday.isoformat()}

    async def change(self, tenant_id: str, shift_id: str, data: dict) -> dict:
        """Move a shift or give it to someone else. A published one goes back to "published" for the
        (new) person to confirm, and they are told."""
        svc = self.svc
        cur = svc.repo.get_row(SHIFTS, tenant_id, shift_id)
        if not cur or cur["status"] == "cancelled":
            raise ShiftError("That shift isn't there any more.")
        fields = self._clean(tenant_id, data or {}, cur)
        was_live = cur["status"] != "draft"
        if was_live:
            fields.update(status="published", published_at=svc.now().isoformat(), responded_at=None, reason=None,
                          reminded_at=None)
        row = svc.repo.update_row(SHIFTS, tenant_id, shift_id, fields)
        members = self._members(tenant_id)
        if was_live and row.get("member_id"):
            await self._tell(tenant_id, row["member_id"], "Your shift",
                             f"{when_text(row['starts_at'], row['ends_at'])}"
                             + (f", {row['section']}" if row.get("section") else "") + ". Please confirm in the app.", shift_id)
        if was_live and cur.get("member_id") and cur["member_id"] != row.get("member_id"):
            await self._tell(tenant_id, cur["member_id"], "Shift changed",
                             f"You're no longer on {when_text(cur['starts_at'], cur['ends_at'])}.", shift_id)
        return self._view(row, members, svc.repo.availability(tenant_id))

    async def cancel(self, tenant_id: str, shift_id: str) -> None:
        svc = self.svc
        cur = svc.repo.get_row(SHIFTS, tenant_id, shift_id)
        if not cur or cur["status"] == "cancelled":
            raise ShiftError("That shift isn't there any more.")
        if cur["status"] == "draft":
            svc.repo.delete_row(SHIFTS, tenant_id, shift_id)
            return
        svc.repo.update_row(SHIFTS, tenant_id, shift_id, {"status": "cancelled"})
        if cur.get("member_id") and cur["status"] in LIVE:
            await self._tell(tenant_id, cur["member_id"], "Shift cancelled",
                             f"Your shift on {when_text(cur['starts_at'], cur['ends_at'])} is cancelled.", shift_id)

    async def publish(self, tenant_id: str, day: Any = None) -> dict:
        """Publish every draft in the week. Each person gets ONE message listing their shifts."""
        svc = self.svc
        _, lo, hi = self._week(day)
        mine: Dict[Optional[str], List[dict]] = {}
        for s in svc.repo.shifts_between(tenant_id, lo.isoformat(), hi.isoformat()):
            if s["status"] != "draft":
                continue
            if svc.repo.cas_row(SHIFTS, tenant_id, s["id"], expected="draft",
                                fields={"status": "published", "published_at": svc.now().isoformat()}):
                mine.setdefault(s.get("member_id"), []).append(s)
        for mid, rows in mine.items():
            if not mid:
                continue
            rows.sort(key=lambda s: s["starts_at"])
            lines = "; ".join(when_text(s["starts_at"], s["ends_at"]) + (f" ({s['section']})" if s.get("section") else "")
                              for s in rows)
            await self._tell(tenant_id, mid, "Your shifts", f"{lines}. Please confirm in the app, or reply CONFIRM.",
                             f"shifts:{rows[0]['id']}")
        return {"published": sum(len(v) for v in mine.values()), "people": len([m for m in mine if m]),
                "open": len(mine.get(None, []))}

    # ── what a person does with their own shifts ─────────────────────────────────────────────
    def mine(self, tenant_id: str, member_id: str) -> dict:
        """A person's own coming shifts, and the open ones they could take."""
        svc = self.svc
        now = svc.now()
        members = self._members(tenant_id)
        rows = svc.repo.shifts_between(tenant_id, (now - timedelta(hours=12)).isoformat(), (now + timedelta(days=28)).isoformat())
        rows = [s for s in rows if _ts(s["ends_at"]) > now]
        own = [self._view(s, members) for s in rows if s.get("member_id") == member_id and s["status"] in STAFF_SEES]
        open_ = [self._view(s, members) for s in rows if not s.get("member_id") and s["status"] == "published"]
        week_lo = self._week(None)[1]
        hours = sum(s["hours"] for s in own if s["status"] in LIVE and _ts(s["starts_at"]) >= week_lo
                    and _ts(s["starts_at"]) < week_lo + timedelta(days=7))
        return {"shifts": own, "open": open_, "to_confirm": sum(1 for s in own if s["status"] == "published"),
                "hours_this_week": round(hours, 2), "availability": self.availability(tenant_id, member_id)}

    def _own(self, tenant_id: str, shift_id: str, member_id: str) -> dict:
        s = self.svc.repo.get_row(SHIFTS, tenant_id, shift_id)
        if not s or s.get("member_id") != member_id or s["status"] not in STAFF_SEES:
            raise ShiftError("That isn't one of your shifts.")
        return s

    def confirm(self, tenant_id: str, shift_id: str, member_id: str) -> dict:
        s = self._own(tenant_id, shift_id, member_id)
        if s["status"] == "confirmed":
            return self._view(s, self._members(tenant_id))
        if not self.svc.repo.cas_row(SHIFTS, tenant_id, shift_id, expected="published",
                                     fields={"status": "confirmed", "responded_at": self.svc.now().isoformat()}):
            raise ShiftError("This shift can't be confirmed any more. Ask your manager.")
        return self._view(self.svc.repo.get_row(SHIFTS, tenant_id, shift_id), self._members(tenant_id))

    async def cant(self, tenant_id: str, shift_id: str, member_id: str, *, sick: bool = False, reason: str = "") -> dict:
        """ "I can't make it" or "I'm sick": the shift is marked, and the owners and managers hear now."""
        svc = self.svc
        s = self._own(tenant_id, shift_id, member_id)
        if _ts(s["ends_at"]) <= svc.now():
            raise ShiftError("That shift is over.")
        new = "sick" if sick else "declined"
        if s["status"] not in LIVE or not svc.repo.cas_row(
                SHIFTS, tenant_id, shift_id, expected=s["status"],
                fields={"status": new, "responded_at": svc.now().isoformat(), "reason": _text(reason, 120) or None}):
            raise ShiftError("You've already told us about this shift.")
        members = self._members(tenant_id)
        who = self._name(members, member_id) or "Someone"
        said = f" ({_text(reason, 120)})" if _text(reason, 120) else ""
        await self._tell(tenant_id, None, "Shift needs cover",
                         f"{who} {'is sick and' if sick else ''} can't work {when_text(s['starts_at'], s['ends_at'])}"
                         .replace("  ", " ") + f"{said}. Give it to someone else or open it to the team.", shift_id)
        return self._view(svc.repo.get_row(SHIFTS, tenant_id, shift_id), members)

    async def offer(self, tenant_id: str, shift_id: str) -> dict:
        """The manager opens a shift to the team: nobody on it, published, first to take it gets it."""
        svc = self.svc
        s = svc.repo.get_row(SHIFTS, tenant_id, shift_id)
        if not s or s["status"] == "cancelled" or _ts(s["ends_at"]) <= svc.now():
            raise ShiftError("That shift can't be offered.")
        row = svc.repo.update_row(SHIFTS, tenant_id, shift_id, {
            "member_id": None, "status": "published", "published_at": svc.now().isoformat(),
            "responded_at": None, "reason": None, "reminded_at": None})
        return self._view(row, self._members(tenant_id))

    async def take(self, tenant_id: str, shift_id: str, member_id: str) -> dict:
        """Take an open shift. One winner; refused if it overlaps a shift the person already has."""
        svc = self.svc
        s = svc.repo.get_row(SHIFTS, tenant_id, shift_id)
        if not s or s.get("member_id") or s["status"] != "published" or _ts(s["ends_at"]) <= svc.now():
            raise ShiftError("Someone else has taken that shift.")
        self._clean(tenant_id, {"member_id": member_id}, s)              # refuses an overlap
        rows = svc.repo.find_rows(SHIFTS, tenant_id, ("published",), id=shift_id)
        if not rows or rows[0].get("member_id") or not self._claim(tenant_id, shift_id, member_id):
            raise ShiftError("Someone else has taken that shift.")
        members = self._members(tenant_id)
        await self._tell(tenant_id, None, "Shift covered",
                         f"{self._name(members, member_id) or 'Someone'} took {when_text(s['starts_at'], s['ends_at'])}.", shift_id)
        return self._view(svc.repo.get_row(SHIFTS, tenant_id, shift_id), members)

    def _claim(self, tenant_id: str, shift_id: str, member_id: str) -> bool:
        """published-and-open → confirmed-and-mine, only if it is still open when the write lands."""
        svc = self.svc
        if not svc.repo.cas_row(SHIFTS, tenant_id, shift_id, expected="published",
                                fields={"status": "confirmed", "member_id": member_id,
                                        "responded_at": svc.now().isoformat()}):
            return False
        return (svc.repo.get_row(SHIFTS, tenant_id, shift_id) or {}).get("member_id") == member_id

    # ── WhatsApp: the team writes to the business's own number ───────────────────────────────
    async def handle_text(self, tenant_id: str, phone: str, text: str) -> bool:
        """ "sick", "can't make my shift", "my shifts", "confirm" from a team member's number.
        False for anyone who isn't on the team, or who has no shift this could be about — the
        message then goes on to whatever would have handled it."""
        t = (text or "").strip()
        if not t or len(t) > 160:
            return False
        sick, cant = bool(_SICK_RE.search(t)), bool(_CANT_RE.search(t))
        mine, confirm = bool(_MINE_RE.match(t)), bool(_CONFIRM_RE.match(t))
        if not (sick or cant or mine or confirm):
            return False
        me = self.member_for_phone(tenant_id, phone)
        if not me:
            return False
        svc = self.svc
        data = self.mine(tenant_id, me["id"])
        coming = [s for s in data["shifts"] if s["status"] in LIVE]
        if not coming and not mine:
            return False
        if mine:
            body = ("Your shifts:\n" + "\n".join(
                f"• {s['when']}" + (f" ({s['section']})" if s["section"] else "")
                + (" — please confirm" if s["status"] == "published" else "") for s in coming)
                    + ("\n\nReply CONFIRM to confirm them, or SICK if you can't work the next one." if coming else "")) \
                if coming else "You have no shifts coming up."
            await svc._say(tenant_id, phone, body)
            return True
        if confirm:
            todo = [s for s in coming if s["status"] == "published"]
            for s in todo:
                self.confirm(tenant_id, s["id"], me["id"])
            await svc._say(tenant_id, phone, f"Thanks — {len(todo)} shift{'' if len(todo) == 1 else 's'} confirmed."
                           if todo else "Your shifts are already confirmed.")
            return True
        nxt = coming[0]
        if _ts(nxt["starts_at"]) - svc.now() > timedelta(hours=36):
            await svc._say(tenant_id, phone, f"Your next shift is {nxt['when']}, more than a day away. "
                                             "Tell your manager, or mark it in the app.")
            return True
        await self.cant(tenant_id, nxt["id"], me["id"], sick=sick, reason="" if sick else _text(t, 120))
        await svc._say(tenant_id, phone, f"Got it — you're off {nxt['when']}. Your manager has been told."
                                         + (" Get well soon." if sick else ""))
        return True

    # ── reminders (background sweep) ─────────────────────────────────────────────────────────
    async def sweep(self, tenant_id: str) -> int:
        """A shift starting in the next two hours is reminded once (marked first)."""
        svc = self.svc
        now = svc.now()
        sent = 0
        for s in svc.repo.shifts_between(tenant_id, now.isoformat(), (now + timedelta(hours=2)).isoformat()):
            if s["status"] not in LIVE or not s.get("member_id") or s.get("reminded_at"):
                continue
            if not svc.repo.cas_row(SHIFTS, tenant_id, s["id"], expected=s["status"],
                                    fields={"reminded_at": now.isoformat()}):
                continue
            await svc._push(tenant_id, s["member_id"], "Shift soon",
                            f"You start at {_ts(s['starts_at']).astimezone(SAST).strftime('%H:%M')}"
                            + (f", {s['section']}" if s.get("section") else "") + ".", tag=s["id"])
            sent += 1
        return sent
