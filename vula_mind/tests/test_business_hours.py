"""Opening hours (migration 158, pre-go-live brief "polish" item). Before this, "are you open?"
always escalated to ask_team — a fact that should be configured once, same class of fix as
delivery coverage (vula/commerce/geo.py, 2026-07-16)."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import hours

OTH_HOURS = {
    "mon": {"open": "08:00", "close": "17:00"}, "tue": {"open": "08:00", "close": "17:00"},
    "wed": {"open": "08:00", "close": "17:00"}, "thu": {"open": "08:00", "close": "17:00"},
    "fri": {"open": "08:00", "close": "17:00"}, "sat": {"open": "08:00", "close": "13:00"},
    "sun": None,
}


def _sast(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=hours.SAST)


# ── hours_verdict (the pure deterministic function) ─────────────────────────────

def test_unconfigured_returns_none_not_a_guess():
    assert hours.hours_verdict(None) is None
    assert hours.hours_verdict({}) is None


def test_open_during_a_configured_window():
    # Tuesday 10:00 — well within 08:00-17:00.
    now = _sast(2026, 9, 15, 10, 0)
    v = hours.hours_verdict(OTH_HOURS, now=now)
    assert v["open"] is True
    assert v["today"]["close"] == "17:00"


def test_closed_before_opening_next_slot_is_later_today():
    # Tuesday 06:00 — before 08:00 opening, same day.
    now = _sast(2026, 9, 15, 6, 0)
    v = hours.hours_verdict(OTH_HOURS, now=now)
    assert v["open"] is False
    assert v["next_open"] == {"day": "today", "time": "08:00"}


def test_closed_after_closing_next_slot_is_tomorrow():
    # Tuesday 18:00 — after 17:00 close.
    now = _sast(2026, 9, 15, 18, 0)
    v = hours.hours_verdict(OTH_HOURS, now=now)
    assert v["open"] is False
    assert v["next_open"] == {"day": "tomorrow", "time": "08:00"}


def test_closed_all_day_sunday_next_slot_is_monday():
    # Sunday — no hours configured at all for that day.
    now = _sast(2026, 9, 20, 12, 0)
    assert now.weekday() == 6  # sanity: this really is a Sunday
    v = hours.hours_verdict(OTH_HOURS, now=now)
    assert v["open"] is False
    assert v["next_open"] == {"day": "tomorrow", "time": "08:00"}


def test_saturday_afternoon_skips_sunday_to_monday():
    # Saturday 14:00 — after the 13:00 Saturday close, Sunday's closed too.
    now = _sast(2026, 9, 19, 14, 0)
    assert now.weekday() == 5
    v = hours.hours_verdict(OTH_HOURS, now=now)
    assert v["open"] is False
    assert v["next_open"]["day"] not in ("today", "tomorrow")  # skipped Sunday to "Monday"
    assert v["next_open"]["time"] == "08:00"


def test_permanently_closed_business_has_no_next_open():
    v = hours.hours_verdict({d: None for d in hours._DAY_KEYS}, now=_sast(2026, 9, 15, 10, 0))
    assert v["open"] is False
    assert v["next_open"] is None


def test_malformed_time_string_degrades_to_closed_not_a_crash():
    v = hours.hours_verdict({"tue": {"open": "not-a-time", "close": "17:00"}},
                            now=_sast(2026, 9, 15, 10, 0))
    assert v["open"] is False


# ── the tool the assistant actually calls ───────────────────────────────────────

def _skill():
    from core.skills.commerce_assistant import CommerceAssistantSkill
    return CommerceAssistantSkill()


def test_unconfigured_tenant_escalates_never_guesses():
    with patch("vula.commerce.order_workflow.get_order_settings",
              return_value={"business_hours": None}):
        out = _skill()._exec_check_business_hours("off-the-hook")
    assert out["verdict"] == "unknown"
    assert "ask_team" in out["instruction"]


@patch("vula.commerce.hours.datetime")
def test_open_tells_the_model_to_say_so_warmly(mock_dt):
    mock_dt.now.return_value = _sast(2026, 9, 15, 10, 0)
    with patch("vula.commerce.order_workflow.get_order_settings",
              return_value={"business_hours": OTH_HOURS}):
        out = _skill()._exec_check_business_hours("off-the-hook")
    assert out["verdict"] == "open"
    assert "Do NOT call ask_team" not in out["instruction"]
    assert "17:00" in out["instruction"]


@patch("vula.commerce.hours.datetime")
def test_closed_is_a_real_answer_not_an_escalation(mock_dt):
    mock_dt.now.return_value = _sast(2026, 9, 15, 18, 0)
    with patch("vula.commerce.order_workflow.get_order_settings",
              return_value={"business_hours": OTH_HOURS, "business_hours_note": "Closed public holidays."}):
        out = _skill()._exec_check_business_hours("off-the-hook")
    assert out["verdict"] == "closed"
    assert "Do NOT call ask_team" in out["instruction"]
    assert "tomorrow" in out["instruction"] and "08:00" in out["instruction"]
    assert "Closed public holidays." in out["instruction"]


# ── after-hours banner (_run_commerce_assistant) ─────────────────────────────────

@pytest.mark.asyncio
async def test_after_hours_banner_prepended_once_then_not_repeated():
    import vula.api.whatsapp as wa
    wa._media_claims_local.clear()

    skill_output = MagicMock(success=True, answer="Sure, here's the menu.", media_url=None)
    with (
        patch("core.skills.loader.get_skill", return_value=AsyncMock(return_value=skill_output)),
        patch("vula.commerce.order_workflow.get_order_settings",
              return_value={"business_hours": OTH_HOURS}),
        patch("vula.commerce.hours.datetime") as mock_dt,
        patch("vula.api.whatsapp._maybe_escalate_and_learn",
              new=AsyncMock(side_effect=lambda tid, ph, txt, ans: ans)),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as reply,
        patch("vula.commerce.service.get_or_create_session",
              new=AsyncMock(return_value={"id": "s1"})),
        patch("vula.commerce.service.format_history", return_value=""),
        patch("vula.commerce.service.get_recent_messages", new=AsyncMock(return_value=[])),
    ):
        mock_dt.now.return_value = _sast(2026, 9, 15, 18, 0)   # closed (after 17:00)
        await wa._run_commerce_assistant("27821234567", "hi", "off-the-hook")
        await wa._run_commerce_assistant("27821234567", "hi again", "off-the-hook")

    sent = [c.args[1] for c in reply.call_args_list]
    assert sum("closed right now" in m for m in sent) == 1, "must not repeat every message"


@pytest.mark.asyncio
async def test_after_hours_banner_uses_the_configured_custom_message():
    import vula.api.whatsapp as wa
    wa._media_claims_local.clear()

    skill_output = MagicMock(success=True, answer="Sure, here's the menu.", media_url=None)
    with (
        patch("core.skills.loader.get_skill", return_value=AsyncMock(return_value=skill_output)),
        patch("vula.commerce.order_workflow.get_order_settings",
              return_value={"business_hours": OTH_HOURS,
                           "after_hours_message": "We're offline till Monday — WhatsApp us then!"}),
        patch("vula.commerce.hours.datetime") as mock_dt,
        patch("vula.api.whatsapp._maybe_escalate_and_learn",
              new=AsyncMock(side_effect=lambda tid, ph, txt, ans: ans)),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as reply,
        patch("vula.commerce.service.get_or_create_session",
              new=AsyncMock(return_value={"id": "s1"})),
        patch("vula.commerce.service.format_history", return_value=""),
        patch("vula.commerce.service.get_recent_messages", new=AsyncMock(return_value=[])),
    ):
        mock_dt.now.return_value = _sast(2026, 9, 15, 18, 0)
        await wa._run_commerce_assistant("27821234568", "hi", "off-the-hook")

    sent = [c.args[1] for c in reply.call_args_list]
    assert any("We're offline till Monday" in m for m in sent)
    assert not any("closed right now" in m for m in sent), "custom message replaces the default"
