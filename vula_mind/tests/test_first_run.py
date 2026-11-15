"""The first-run welcome (vula/commerce/first_run.py): optional, closed for the whole business
when skipped, built from what the set-up jobs found, and a customer-answer preview that can't
order, send, save or guess."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from core.skills.base import SkillOutput
from tests.fake_supabase import FakeSupabase
from vula.commerce import first_run, service, site_import

T = "deli"
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def env(monkeypatch):
    db = FakeSupabase({
        "vula_tenant_config": [{"tenant_id": T, "created_at": (NOW - timedelta(days=2)).isoformat()},
                               {"tenant_id": "old", "created_at": (NOW - timedelta(days=60)).isoformat()}],
        "commerce_invoice_settings": [{"tenant_id": T, "logo_url": "https://x/logo.png", "accent_color": "#123456"}],
        "vula_background_jobs": [],
    })
    db.primary_keys["vula_background_jobs"] = ["tenant_id", "kind"]
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(site_import, "_db", lambda: db)
    site_import._STATUS.clear()
    first_run._previews.clear()
    profile = {"answers": {"hours": "Mon–Fri 7:30–17:00"}, "answered": 1, "total": 12,
               "drafts": {"area": {"answer": "Sea Point, Green Point and Gardens", "source": {"kind": "website"}}}}
    monkeypatch.setattr("vula.commerce.business_profile.status", lambda t: profile)
    monkeypatch.setattr(first_run, "_setup_complete", lambda t: False)
    return db


def test_questions_to_try_come_from_the_owners_answers_with_fixed_wording():
    out = first_run.try_asking({"hours": "7 to 5", "payment": "EFT"},
                               {"area": {"answer": "We deliver to Sea Point, Green Point"}, "faq": {"answer": "x"}})
    assert out == [{"key": "hours", "question": "What are your opening hours?", "ready": True},
                   {"key": "area", "question": "Do you deliver to Sea Point?", "ready": False},
                   {"key": "payment", "question": "How can I pay?", "ready": True}]
    assert first_run.try_asking({"area": "1234 Long Street 8001 and more"}, {})[0]["question"] == "Where do you deliver?"
    assert first_run.try_asking({}, {}) == []


def test_summary_combines_the_website_the_documents_the_profile_and_the_brand(env):
    site_import._save(T, {"running": True, "step": "reading 12 pages", "products_found": 41,
                          "coverage": {"policies": {"terms": "https://x/terms"}}, "doc_ids": ["a"]})
    site_import._save(T, {"running": False, "files": [{"name": "Terms.pdf", "status": "done", "drafts": 2}], "drafts": 2},
                      "profile_docs")
    out = first_run.summary(T, now=NOW)
    assert out["show"] is True and out["running"] is True and out["closed"] is None
    assert out["website"]["products_found"] == 41 and out["website"]["coverage"]["policies"]["terms"]
    assert "doc_ids" not in out["website"]                          # internals stay internal
    assert out["documents"]["files"][0]["drafts"] == 2
    assert out["profile"] == {"answered": 1, "total": 12, "drafts": 1}
    assert out["brand"] == {"logo_url": "https://x/logo.png", "accent_color": "#123456", "set": True}
    assert [t["key"] for t in out["try_asking"]] == ["hours", "area"]
    assert "not_read" not in out


def test_skipping_closes_it_for_the_whole_business_and_no_other(env):
    first_run.close(T, "skipped")
    site_import._STATUS.clear()                                      # another worker / device
    out = first_run.summary(T, now=NOW)
    assert out["show"] is False and out["closed"] == "skipped"
    env.rows["vula_tenant_config"].append({"tenant_id": "new", "created_at": NOW.isoformat()})
    assert first_run.summary("new", now=NOW)["show"] is True
    with pytest.raises(ValueError):
        first_run.close(T, "later")


def test_it_does_not_show_for_an_older_business_or_a_finished_set_up(env, monkeypatch):
    assert first_run.summary("old", now=NOW)["show"] is False
    monkeypatch.setattr(first_run, "_setup_complete", lambda t: True)
    assert first_run.summary(T, now=NOW)["show"] is False


def test_a_part_that_cannot_be_read_is_named(env, monkeypatch):
    def boom(t):
        raise RuntimeError("db down")
    monkeypatch.setattr("vula.commerce.business_profile.status", boom)
    out = first_run.summary(T, now=NOW)
    assert out["not_read"] == ["profile"] and out["profile"]["answered"] is None


# ── the preview ─────────────────────────────────────────────────────────────────────────────

class _Reasoning:
    def __init__(self):
        self.seen = None
        self.audience = None
        self.voice = None

    async def __call__(self, inp):
        from core import voice
        from vula.ingestion.pipeline import _KB_AUDIENCE
        self.seen, self.audience, self.voice = inp, _KB_AUDIENCE.get(), voice._CURRENT.get()
        return SkillOutput(answer="We're open Mon–Fri 7:30–17:00.", skill_name="reasoning",
                           sources=[{"type": "kb", "filename": "Business profile — for customers", "text": "secret"}])


@pytest.mark.asyncio
async def test_the_preview_answers_as_a_customer_would_be_answered(env):
    skill = _Reasoning()
    with patch("core.skills.loader.get_skill", return_value=skill) as get:
        out = await first_run.preview_customer_answer(T, "  What are your   opening hours? ")
    get.assert_called_once_with("reasoning")                          # the skill with no tools
    assert skill.audience == "public"                                  # customer-visible documents only
    assert skill.voice.audience == "customer"
    assert skill.seen.question == "What are your opening hours?"
    assert skill.seen.metadata["strict_grounding"] is True and "customer_phone" not in skill.seen.metadata
    assert out == {"answer": "We're open Mon–Fri 7:30–17:00.", "sources": "Business profile — for customers",
                   "grounded": True}
    assert "commerce_conversation_messages" not in env.rows and "vula_kb_turns" not in env.rows


@pytest.mark.asyncio
async def test_the_preview_is_limited(env):
    with patch("core.skills.loader.get_skill", return_value=_Reasoning()):
        for _ in range(first_run.PREVIEWS_PER_HOUR):
            await first_run.preview_customer_answer(T, "hours?")
        with pytest.raises(PermissionError):
            await first_run.preview_customer_answer(T, "hours?")
        await first_run.preview_customer_answer("other", "hours?")     # per business
    for bad in ("", "x" * 301):
        with pytest.raises(ValueError):
            await first_run.preview_customer_answer(T, bad)


@pytest.mark.asyncio
async def test_strict_grounding_in_the_preview_declines_an_unknown_business_fact_without_the_web():
    from core.skills.base import BUSINESS_FACT_NOT_ON_FILE_CUSTOMER, SkillInput
    from core.skills.reasoning import ReasoningSkill
    pipeline = MagicMock()
    pipeline.query = AsyncMock(return_value=[])
    web = AsyncMock()
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipeline), \
         patch("core.skills.loader.get_skill", return_value=web), patch("vula.kb_learning.record_turn"):
        out = await ReasoningSkill().run(SkillInput(question="Do you deliver to Paarl?", tenant_id=T,
                                                    metadata={"strict_grounding": True}))
    assert out.answer == BUSINESS_FACT_NOT_ON_FILE_CUSTOMER
    web.assert_not_awaited()


# ── the routes: owner or manager only ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_restricted_login_cannot_use_the_welcome(env):
    from vula.api import me
    req = MagicMock()
    req.headers = {"authorization": "Bearer tok"}
    staff = {"full": False, "access": ["orders"]}
    with patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value=staff)):
        for call in (lambda: me.first_run_summary(T, req),
                     lambda: me.first_run_preview(T, me.PreviewIn(question="hours?"), req),
                     lambda: me.first_run_close(T, me.CloseIn(reason="skipped"), req)):
            with pytest.raises(HTTPException) as e:
                await call()
            assert e.value.status_code == 403
    with patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value={"full": True, "access": []})):
        assert (await me.first_run_close(T, me.CloseIn(reason="skipped"), req))["closed"] == "skipped"
        with pytest.raises(HTTPException) as e:
            await me.first_run_close(T, me.CloseIn(reason="never"), req)
        assert e.value.status_code == 400
