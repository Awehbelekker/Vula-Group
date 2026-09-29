"""PR F: the Monday tenant-health email. Real tenant shapes from 29 Sep: DIGG busy with documents
waiting for a project, Off the Hook fine, kelp-boardbags silent for months, one suspended."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from vula import master_digest as md

TENANTS = [
    {"tenant_id": "off-the-hook", "display_name": "Off the Hook", "health": "green",
     "health_reasons": [], "last_activity": "2026-09-28T10:00:00Z"},
    {"tenant_id": "kelp-boardbags", "display_name": "Kelp", "health": "amber", "dormant": True,
     "health_reasons": ["No messages yet"], "last_activity": None},
    {"tenant_id": "digg-demo", "display_name": "DIGG", "health": "amber", "open_escalations": 2,
     "health_reasons": ["2 unanswered question(s) for the team"]},
    {"tenant_id": "medusa", "display_name": "Medusa", "health": "off", "health_reasons": ["Suspended"]},
]


@pytest.fixture
def shapes(monkeypatch):
    monkeypatch.setattr("vula.api.master.tenant_overview", lambda: {"tenants": [dict(t) for t in TENANTS]})
    q = MagicMock()
    q.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"tenant_id": "digg-demo"}] * 3)
    monkeypatch.setattr(md, "_client", lambda: MagicMock(table=lambda _t: q))
    monkeypatch.setattr("vula.startup_checks.check_schema", lambda: [])


def test_worst_first_with_waiting_documents_and_dormant(shapes):
    d = md.build()
    assert [r["tenant_id"] for r in d["tenants"]] == ["digg-demo", "kelp-boardbags", "off-the-hook", "medusa"]
    digg = d["tenants"][0]
    assert digg["stuck_documents"] == 3 and "3 document(s) waiting for a project" in digg["reasons"]
    assert d["dormant"] == ["Kelp"]                       # the suspended one isn't nagged about
    assert d["counts"] == {"red": 0, "amber": 2, "green": 1, "off": 1}
    text = md.render_text(d)
    assert text.startswith("Vula weekly — 0 need attention, 2 to watch, 1 fine, 1 suspended")
    assert "🟢 Off the Hook" in text and "No messages in 30 days: Kelp" in text


def test_names_are_escaped_in_the_email(shapes, monkeypatch):
    monkeypatch.setattr("vula.api.master.tenant_overview", lambda: {"tenants": [
        {"tenant_id": "x", "display_name": "<b>Evil</b>", "health": "green", "health_reasons": []}]})
    assert "<b>Evil</b>" not in md.render_html(md.build())


@pytest.mark.asyncio
async def test_sent_once_a_week_to_team_email(shapes, monkeypatch):
    monkeypatch.setattr("config.settings.team_email", "team@vula.ai")
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("vula.api.email._send", sent)
    monkeypatch.setattr(md, "already_sent", lambda week: False)
    res = await md.send(now=datetime(2026, 10, 5, 7, 30, tzinfo=md.SAST))
    assert res == {"sent": True, "week": "2026-W41", "reason": None}
    assert sent.await_args.args[0] == "team@vula.ai"
    monkeypatch.setattr(md, "already_sent", lambda week: True)
    sent.reset_mock()
    assert (await md.send(now=datetime(2026, 10, 5, 8, 0, tzinfo=md.SAST)))["sent"] is False
    sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_nothing_sent_without_team_email(monkeypatch):
    monkeypatch.setattr("config.settings.team_email", "")
    assert (await md.send())["reason"] == "TEAM_EMAIL is not set"


def test_unknown_sent_state_does_not_send(monkeypatch):
    def boom():
        raise RuntimeError("supabase down")
    monkeypatch.setattr(md, "_client", boom)
    assert md.already_sent("2026-W41") is True


def test_due_only_monday_morning():
    assert md.due(datetime(2026, 10, 5, 7, 0, tzinfo=md.SAST))
    assert not md.due(datetime(2026, 10, 5, 11, 0, tzinfo=md.SAST))
    assert not md.due(datetime(2026, 10, 6, 8, 0, tzinfo=md.SAST))
