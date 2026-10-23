"""Off the Hook, 23 Oct: the team forwarding promotions into its own WhatsApp line.

Ian forwarded about 20 of OTH's old marketing posts into OTH's Vula line. Vula answered each as a
customer: "I'm not able to file documents on this line", tap menus, "we'll be in touch", and the
"FRESH FROM THE OCEAN" post put 2 × Hake Fillets in his cart. All 20 ran at once on the local
model, so each took 135–170 s.
"""
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api import whatsapp as wa
from vula.commerce import promotions

SWORDFISH = ("**FRESH EXPORT-QUALITY SWORDFISH HAS ARRIVED!**\n\nPremium skinless & boneless "
             "swordfish loins at R320/kg. Order before 12pm for delivery tomorrow.")
CHICKEN = "Good morning \nLast chance to order some fresh chicken, please send in your order"


# ── the webhook ──────────────────────────────────────────────────────────────

def _msg(run, i, kind, *, body="", forwarded=True, phone="27645755210"):
    m = {"from": phone, "id": f"wamid.{run}.{i}", "type": kind}
    if kind == "text":
        m["text"] = {"body": body}
    else:
        m[kind] = {"id": f"media{i}", "mime_type": "image/jpeg", **({"caption": body} if body else {})}
    if forwarded:
        m["context"] = {"forwarded": True}
    return {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "pn1"},
                                              "messages": [m]}}]}]}


def _post(messages, *, team=True):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(wa.router, prefix="/v1/whatsapp")
    saved, commerce, media = AsyncMock(), AsyncMock(), AsyncMock()
    with patch.object(wa.settings, "vula_fb_app_secret", ""), patch.object(wa.settings, "debug", True), \
         patch.object(wa, "_resolve_number_route", return_value=("off-the-hook", "commerce")), \
         patch("vula.api.tenants.is_active", return_value=True), \
         patch.object(wa, "_is_team", return_value=team), \
         patch.object(wa, "_is_tenant_owner", return_value=team), \
         patch.object(wa, "_mark_read_and_typing", new=AsyncMock()), \
         patch.object(wa, "_save_forwarded_promo", new=saved), \
         patch.object(wa, "_handle_commerce_message", new=commerce), \
         patch.object(wa, "_handle_image_or_video", new=media), \
         patch("vula.commerce.service._client", side_effect=RuntimeError("no db")):
        c = TestClient(app)
        for m in messages:
            assert c.post("/v1/whatsapp/webhook", json=m).status_code == 200
    return saved, commerce, media


def test_a_team_forward_is_saved_and_never_answered_as_a_customer():
    wa._PROMO_BURSTS.clear()
    run = uuid.uuid4().hex
    saved, commerce, media = _post([
        _msg(run, 1, "text", body=CHICKEN),
        _msg(run, 2, "image", body=SWORDFISH),
        _msg(run, 3, "image"),                       # a picture with no words, mid-burst
    ])
    assert not commerce.called and not media.called
    calls = [c.args for c in saved.call_args_list]
    assert calls[0] == ("off-the-hook", "27645755210", CHICKEN, None, None)
    assert calls[1] == ("off-the-hook", "27645755210", SWORDFISH, "media2", "image/jpeg")
    assert calls[2][2] == ""
    wa._PROMO_BURSTS.clear()


def test_a_team_photo_without_words_outside_a_burst_is_handled_as_usual():
    """A supplier invoice photo the team forwards is still a document, not a promotion."""
    wa._PROMO_BURSTS.clear()
    saved, commerce, media = _post([_msg(uuid.uuid4().hex, 1, "image")])
    assert not saved.called and media.called


def test_a_customer_forward_is_answered_not_saved():
    wa._PROMO_BURSTS.clear()
    saved, commerce, media = _post([_msg(uuid.uuid4().hex, 1, "text", body=SWORDFISH,
                                         phone="27820001111")], team=False)
    assert not saved.called and commerce.called


def test_an_ordinary_team_message_is_not_a_promotion():
    saved, commerce, media = _post([_msg(uuid.uuid4().hex, 1, "text", body="2kg hake for Thandi",
                                         forwarded=False)])
    assert not saved.called and commerce.called


# ── one acknowledgement per burst ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_acknowledgement_for_a_burst(monkeypatch):
    wa._PROMO_BURSTS.clear()
    monkeypatch.setattr(wa, "_PROMO_ACK_DELAY_S", 0.02)
    rows = iter([{"id": "p1"}, {"id": "p2"}, {"id": "p0", "duplicate": True}, {"id": "p3"}])
    monkeypatch.setattr(promotions, "save", lambda *a, **k: next(rows))
    sent, asked = [], []
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(side_effect=lambda p, m, *a, **k: sent.append(m) or True))
    monkeypatch.setattr("vula.open_questions.ask", lambda t, p, k, ref, prompt="": asked.append((k, ref)))
    for body in (CHICKEN, SWORDFISH, CHICKEN, "", "FRESH TUNA – LIMITED STOCK!"):
        await wa._save_forwarded_promo("off-the-hook", "27645755210", body)
    await asyncio.sleep(0.1)
    assert len(sent) == 1
    assert "Saved 3 promotions" in sent[0] and "1 was already saved" in sent[0]
    assert "1 picture had no text" in sent[0] and "*examples only*" in sent[0]
    assert asked == [("promo_scope", "p1,p2,p3")]
    assert not wa._PROMO_BURSTS


@pytest.mark.asyncio
async def test_examples_only_and_undo(monkeypatch):
    sent, changed, closed = [], [], []
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(side_effect=lambda p, m, **k: sent.append(m) or True))
    monkeypatch.setattr(promotions, "set_source",
                        lambda t, ids, src: changed.append((ids, src)) or len(ids))
    monkeypatch.setattr("vula.open_questions.close", lambda qid, answer="", status="answered": closed.append(qid))
    q = {"id": "q1", "kind": "promo_scope", "ref_id": "p1,p2"}
    assert await wa._try_open_question("off-the-hook", "27645755210", "Examples only", q)
    assert changed[-1] == (["p1", "p2"], "example") and "won't be shown" in sent[-1]
    assert await wa._try_open_question("off-the-hook", "27645755210", "undo", q)
    assert changed[-1] == (["p1", "p2"], "removed")
    assert not await wa._try_open_question("off-the-hook", "27645755210", "2kg hake please", q)
    assert closed == ["q1", "q1"]


# ── one message at a time per sender ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_senders_messages_run_one_after_another():
    order = []

    async def work(n):
        order.append(f"start{n}")
        await asyncio.sleep(0.01)
        order.append(f"end{n}")

    await asyncio.gather(wa._one_at_a_time("oth", "a", work(1)), wa._one_at_a_time("oth", "a", work(2)))
    assert order == ["start1", "end1", "start2", "end2"]
    assert not wa._SENDER_LOCKS


@pytest.mark.asyncio
async def test_different_senders_still_run_together():
    order = []

    async def work(n):
        order.append(f"start{n}")
        await asyncio.sleep(0.01)
        order.append(f"end{n}")

    await asyncio.gather(wa._one_at_a_time("oth", "a", work(1)), wa._one_at_a_time("oth", "b", work(2)))
    assert order[:2] == ["start1", "start2"]


@pytest.mark.asyncio
async def test_the_forwarded_flag_reaches_the_assistant():
    seen = []

    async def work():
        seen.append(wa._FORWARDED.get())

    await wa._one_at_a_time("oth", "c", work(), forwarded=True)
    await wa._one_at_a_time("oth", "c", work())
    assert seen == [True, False]


# ── promotions store ─────────────────────────────────────────────────────────

class _Q:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def __getattr__(self, name):
        def call(*a, **k):
            self.log.append((name, a))
            return self
        return call

    def execute(self):
        return MagicMock(data=self.rows)


def _db(rows, log):
    db = MagicMock()
    db.table.side_effect = lambda n: _Q(rows, log)
    return db


def test_customers_get_dated_promotions_with_prices_masked(monkeypatch):
    log = []
    rows = [{"id": "p1", "body": SWORDFISH, "created_at": "2026-10-21T09:51:14+00:00"},
            {"id": "p2", "body": "We also have more fresh Angel fish coming in 🤤 at R100 / kg",
             "created_at": "2026-10-23T09:51:28+00:00"}]
    monkeypatch.setattr(promotions, "_client", lambda: _db(rows, log))
    text = promotions.for_customers("off-the-hook")
    assert "From the 21 Oct promotion:" in text and "From the 23 Oct promotion:" in text
    assert "R320" not in text and "R100" not in text and "(price on request)" in text
    assert ("in_", ("source", ["forwarded"])) in log          # "examples only" posts are left out
    assert any(n == "gte" and a[0] == "created_at" for n, a in log)


def test_the_same_post_forwarded_twice_is_kept_once(monkeypatch):
    log = []
    monkeypatch.setattr(promotions, "_client",
                        lambda: _db([{"id": "p1", "body": "Good morning  \nLast chance to order some "
                                                          "fresh chicken, please send in your order"}], log))
    row = promotions.save("off-the-hook", CHICKEN, by="27645755210")
    assert row["duplicate"] and not any(n == "insert" for n, a in log)


def test_mask_prices():
    assert promotions.mask_prices("Swordfish R320/kg, platters R1,250 and trays R 2 500.00") == (
        "Swordfish (price on request), platters (price on request) and trays (price on request)")


# ── the assistants ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_forwarded_post_never_fills_the_cart():
    from core.skills.commerce_assistant import CommerceAssistantSkill
    skill = CommerceAssistantSkill()
    with patch("vula.commerce.service.list_products", new=AsyncMock()) as lp:
        res = await skill._exec_add_to_cart("off-the-hook", "s1", "27820001111",
                                            {"product": "Hake Fillets", "quantity": 2},
                                            {"forwarded": True})
    assert res["status"] == "not_added" and not lp.called


def test_current_specials_is_fenced_and_price_free(monkeypatch):
    from core.skills.commerce_assistant import CommerceAssistantSkill
    monkeypatch.setattr(promotions, "for_customers",
                        lambda t: "- From the 21 Oct promotion: Swordfish (price on request)")
    res = CommerceAssistantSkill()._exec_current_specials("off-the-hook")
    assert "BEGIN BUSINESS_PROMOTIONS" in res["specials"] and "list_products" in res["note"]
    monkeypatch.setattr(promotions, "for_customers", lambda t: "")
    assert "No promotions" in CommerceAssistantSkill()._exec_current_specials("off-the-hook")["message"]


def test_recent_promotions_gives_the_owner_their_own_examples(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    monkeypatch.setattr(promotions, "examples", lambda t: [SWORDFISH, CHICKEN])
    res = CommerceAdminSkill()._recent_promotions("off-the-hook")
    assert len(res["examples"]) == 2 and "SWORDFISH" in res["examples"][0]


def test_tools_are_read_only_in_dry_run():
    from core import dry_run
    assert {"current_specials", "recent_promotions"} <= dry_run.READ_ONLY
