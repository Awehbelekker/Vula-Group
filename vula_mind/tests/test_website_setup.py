"""Website setup (5 Nov 2026, Ian: "get as much details [from their website], but when the
client uses their number it should ask the questions").

- profile_from_site: an answer drafted from the website is kept only when the sentence it quotes
  is really on the page.
- business_profile ("set up my profile"): a drafted question asks "Your website says … reply
  yes"; "yes" keeps the draft, anything else replaces it. (The new business's own setup
  questions, vula/onboard_wa.py, do the same: tests/test_onboard_wa.py.)
"""
from types import SimpleNamespace

import pytest

from vula.commerce import business_profile as bp
from vula.commerce.profile_from_site import grounded, pick_pages

PAGES = [
    ("https://shopeazy.example/", "ShopEazy", "We sell school uniforms and stationery in Durbanville."),
    ("https://shopeazy.example/delivery", "Delivery",
     "We deliver across the Northern Suburbs of Cape Town.  Orders before 2pm go out the next working day."),
    ("https://shopeazy.example/returns", "Returns", "Unworn items can be returned within 14 days with the slip."),
]
VALID = {"what_we_do", "area", "lead_times", "cancellations", "hours", "payment"}


def test_only_answers_whose_quote_is_on_the_page_are_kept():
    drafts = {
        "area": {"answer": "Northern Suburbs of Cape Town", "quote": "We deliver across the Northern Suburbs of Cape Town."},
        "lead_times": {"answer": "Next working day if ordered before 2pm",
                       "quote": "orders before 2pm go out the next working day."},          # case/space differ
        "hours": {"answer": "Mon–Fri 8–5", "quote": "Open Monday to Friday, 8am to 5pm."},   # not on the site
        "payment": {"answer": "EFT", "quote": "EFT"},                                         # too short to mean anything
        "bogus_key": {"answer": "x", "quote": "We sell school uniforms and stationery"},
    }
    out = grounded(drafts, PAGES, VALID)
    assert set(out) == {"area", "lead_times"}
    assert out["area"]["url"] == "https://shopeazy.example/delivery"


def test_the_useful_pages_are_read_first():
    pages = [("https://s.example/blog/x", "Blog", "a" * 300)] + PAGES
    assert [p[0] for p in pick_pages(pages)][:3] == [
        "https://shopeazy.example/", "https://shopeazy.example/delivery", "https://shopeazy.example/returns"]


@pytest.fixture
def profile(monkeypatch):
    state = {"answers": {}, "drafts": {}, "saved": []}
    qs = [{"key": "area", "q": "Where do you work or deliver?"}, {"key": "hours", "q": "What are your hours?"}]
    monkeypatch.setattr(bp, "questions", lambda bt: qs)
    monkeypatch.setattr(bp, "_business_type", lambda tid: "retail")
    monkeypatch.setattr(bp, "get_answers", lambda tid: dict(state["answers"]))
    monkeypatch.setattr(bp, "structured_hours", lambda tid: "")
    monkeypatch.setattr(bp, "get_public_keys", lambda tid: ["area", "hours"])
    monkeypatch.setattr(bp, "get_drafts", lambda tid: {k: v for k, v in state["drafts"].items() if k not in state["answers"]})

    async def save(tid, updates, by="", public_keys=None):
        state["answers"].update(updates)
        state["saved"].append(updates)
        return {"saved": True}
    monkeypatch.setattr(bp, "save_answers", save)
    bp._ACTIVE.clear()
    return state


@pytest.mark.asyncio
async def test_set_up_my_profile_offers_the_website_draft_and_yes_keeps_it(profile):
    profile["drafts"] = {"area": {"answer": "Northern Suburbs of Cape Town", "quote": "…", "url": "u"}}
    reply = await bp.handle_interview("shopeazy", "27821234567", "set up my profile")
    assert "drafted 1 of the answers" in reply
    assert "Your website says: “Northern Suburbs of Cape Town”" in reply and "*yes*" in reply
    reply = await bp.handle_interview("shopeazy", "27821234567", "yes")           # keep the draft
    assert profile["saved"] == [{"area": "Northern Suburbs of Cape Town"}]
    assert "What are your hours?" in reply and "Your website says" not in reply
    await bp.handle_interview("shopeazy", "27821234567", "Mon-Fri 8 to 5")
    assert profile["saved"][-1] == {"hours": "Mon-Fri 8 to 5"}


@pytest.mark.asyncio
async def test_a_different_answer_replaces_the_draft_and_a_hello_starts_nothing(profile):
    profile["drafts"] = {"area": {"answer": "Northern Suburbs", "quote": "…", "url": "u"}}
    assert await bp.handle_interview("shopeazy", "27821234567", "hi") is None      # #277's setup asks, not this
    await bp.handle_interview("shopeazy", "27821234567", "set up my profile")
    await bp.handle_interview("shopeazy", "27821234567", "All of Cape Town now")
    assert profile["saved"] == [{"area": "All of Cape Town now"}]
