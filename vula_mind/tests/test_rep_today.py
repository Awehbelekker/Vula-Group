"""A rep's day (2026-09-29, Ian: "sales reps need their appointments for the day and a note
button / minutes on the meeting"). Appointments are the rep's own diary; minutes run through the
WhatsApp voice-note pipeline (log_meeting) and are linked to the appointment."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from tests.test_job_costing import FakeDB as _Base, _Q as _BaseQ
from vula.api import commerce
from vula.commerce import service

TID = "gerflor"
REP = "27821112222"


class _Lt:
    def __init__(self, v): self.v = v
    def __eq__(self, other): return other is not None and str(other) < self.v


class _Q(_BaseQ):
    def lt(self, col, val):
        self.filters.append((col, _Lt(val)))
        return self


class FakeDB(_Base):
    def table(self, name):
        return _Q(self, name)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({"commerce_bookings": [], "vula_filed_documents": []})
    monkeypatch.setattr(service, "_client", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_a_reps_appointment_goes_in_their_own_diary_and_today(db):
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(second=0, microsecond=0)
    out = await commerce.admin_rep_add_appointment(TID, {
        "rep_phone": "082 111 2222", "customer_name": "KAA Architecture", "start": _iso(soon),
        "duration_min": 45, "location": "Woodstock studio"})
    b = out["booking"]
    assert b["booked_by"] == REP and b["location"] == "Woodstock studio"
    # a shop customer's booking at the same time doesn't clash with the rep's diary
    db.tables["commerce_bookings"].append({"tenant_id": TID, "id": "shop1", "start_at": _iso(soon + timedelta(minutes=90)),
                                           "end_at": _iso(soon + timedelta(minutes=120)), "status": "confirmed"})
    again = await commerce.admin_rep_add_appointment(TID, {
        "rep_phone": REP, "customer_name": "Tersia", "start": _iso(soon + timedelta(minutes=90))})
    assert again["booking"]["customer_name"] == "Tersia"
    # but the rep's own double-booking does
    with pytest.raises(HTTPException) as e:
        await commerce.admin_rep_add_appointment(TID, {"rep_phone": REP, "customer_name": "X", "start": _iso(soon + timedelta(minutes=10))})
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_minutes_on_an_appointment_mark_it_done_and_link(db, monkeypatch):
    now = datetime.now(timezone.utc)
    db.tables["commerce_bookings"].append({"tenant_id": TID, "id": "b1", "customer_name": "Darian, KAA",
                                           "customer_phone": "27830000000", "start_at": _iso(now),
                                           "end_at": _iso(now + timedelta(hours=1)), "status": "confirmed",
                                           "booked_by": REP})
    seen = {}

    async def fake_log(self, tid, args, ctx):
        seen.update(args=args, ctx=ctx)
        db.tables["vula_filed_documents"].append({"tenant_id": tid, "id": "m1", "category": "meeting_notes",
                                                  "filed_by": ctx["phone"], "fields": {}, "summary": "Samples for KAA",
                                                  "created_at": _iso(datetime.now(timezone.utc))})
        return {"logged": True, "action_items": ["Send Creation 30 samples (Friday)"], "pdf_sent": True}
    monkeypatch.setattr("core.skills.commerce_admin.CommerceAdminSkill._log_meeting", fake_log)
    out = await commerce.admin_log_meeting(TID, {"notes": "Darian wants Creation 30 samples by Friday.",
                                                  "rep_phone": "0821112222", "booking_id": "b1"})
    assert out["logged"] and seen["ctx"]["phone"] == REP and seen["args"]["contact_name_or_phone"] == "27830000000"
    assert db.tables["commerce_bookings"][0]["status"] == "completed"
    assert db.tables["vula_filed_documents"][0]["fields"]["booking_id"] == "b1"
    day = await commerce.admin_rep_today(TID, rep_phone=REP)
    assert day["appointments"][0]["has_minutes"] is True
    assert day["notes"][0]["booking_id"] == "b1"


@pytest.mark.asyncio
async def test_empty_minutes_are_refused(db):
    with pytest.raises(HTTPException) as e:
        await commerce.admin_log_meeting(TID, {"notes": " ", "rep_phone": REP})
    assert e.value.status_code == 400


def test_one_logo_size_for_every_surface():
    from vula.commerce.brand_sizes import logo_px, ICON_FILL
    assert logo_px("pdf", "xl")[0] > logo_px("pdf", "lg")[0] > logo_px("pdf", "md")[0] > logo_px("pdf", "sm")[0]
    assert logo_px("email", None) == logo_px("email", "md") and logo_px("menu", "bogus") == logo_px("menu", "md")
    assert ICON_FILL["xl"] > ICON_FILL["sm"]
    from vula.api.email import _brand_parts
    small, _, _ = _brand_parts({"name": "G", "logo_url": "https://x/l.png", "logo_size": "sm"}, "g")
    large, _, _ = _brand_parts({"name": "G", "logo_url": "https://x/l.png", "logo_size": "xl"}, "g")
    assert "max-height:36px" in small and "max-height:72px" in large
