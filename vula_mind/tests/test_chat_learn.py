"""Learning a business's tone and common answers from its WhatsApp chat exports
(vula/commerce/chat_learn.py, 20 Oct 2026 — SeedleMe's Matt offered his chat history).

The exports below follow the real Android and iPhone formats. Customers' names and numbers are
invented."""
import base64
import io
import zipfile
from unittest.mock import AsyncMock

import pytest

from vula.commerce import chat_learn as cl

ANDROID = """15/10/2026, 09:12 - Messages and calls are end-to-end encrypted. No one outside of this chat, not even WhatsApp, can read or listen to them.
15/10/2026, 09:12 - Thandi Mokoena: Hi, do you make round basins in charcoal?
15/10/2026, 09:20 - Matt Wilkinson: Morning Thandi! Yes we do, the Round Drop-In comes in charcoal, ivory and sandstone.
Made to order takes about 3 weeks.
15/10/2026, 09:21 - Thandi Mokoena: <Media omitted>
15/10/2026, 09:22 - Thandi Mokoena: How much is delivery to Durbanville? My number is 082 555 1234
15/10/2026, 09:30 - Matt Wilkinson: Delivery in Cape Town is R350, we'll book it once the basin is ready 👍
15/10/2026, 09:31 - Thandi Mokoena: Thanks!
15/10/2026, 09:32 - Matt Wilkinson: Pleasure, shout if you need anything
"""

IPHONE = """[16/10/2026, 11:02:10] Matt Wilkinson: ‎Messages and calls are end-to-end encrypted.
[16/10/2026, 11:02:10] Pieter: Can I collect from your workshop?
[16/10/2026, 11:05:41] Matt Wilkinson: Ja for sure, collection from our Montague Gardens workshop, Mon to Fri 8 to 4.
[16/10/2026, 11:06:02] Pieter: ‎image omitted
[16/10/2026, 11:07:00] Pieter: Do you need a deposit?
[16/10/2026, 11:09:00] Matt Wilkinson: We take 50% upfront on made to order pieces, balance before delivery.
"""


def _zip(text: str) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("_chat.txt", text)
    return base64.b64encode(buf.getvalue()).decode()


def test_both_export_formats_are_read_and_system_lines_dropped():
    a = cl.parse_export(ANDROID)
    assert [m["sender"] for m in a][:2] == ["Thandi Mokoena", "Matt Wilkinson"]
    assert "Made to order takes about 3 weeks." in a[1]["text"]          # continuation line kept
    assert not any("omitted" in m["text"] or "encrypted" in m["text"] for m in a)
    i = cl.parse_export(IPHONE)
    assert [m["sender"] for m in i] == ["Pieter", "Matt Wilkinson", "Pieter", "Matt Wilkinson"]
    chats = cl.read_files([{"name": "WhatsApp Chat - Thandi.txt", "text": ANDROID},
                           {"name": "WhatsApp Chat - Pieter.zip", "b64": _zip(IPHONE)}])
    assert len(chats) == 2


def test_the_business_side_is_found_from_the_team_or_across_chats():
    chats = cl.read_files([{"text": ANDROID}, {"text": IPHONE}])
    assert cl.business_sender(chats, ["Matt Wilkinson", "SeedleMe"]) == "Matt Wilkinson"
    assert cl.business_sender(chats, []) == "Matt Wilkinson"           # in both chats
    assert cl.business_sender(chats[:1], []) is None                   # one chat: ask
    assert set(cl.all_senders(chats[:1])) == {"Thandi Mokoena", "Matt Wilkinson"}


def test_tone_comes_only_from_the_business_with_contact_details_removed():
    chats = cl.read_files([{"text": ANDROID}, {"text": IPHONE}])
    samples = cl.voice_samples(chats, "Matt Wilkinson")
    assert any(s.startswith("Morning Thandi! Yes we do") for s in samples)
    assert not any("Durbanville" in s or "082" in s for s in samples)   # customer lines never used
    assert "Thanks!" not in samples


def test_question_and_reply_pairs_with_numbers_redacted():
    chats = cl.read_files([{"text": ANDROID}, {"text": IPHONE}])
    pairs = cl.qa_pairs(chats, "Matt Wilkinson")
    qs = [p["q"] for p in pairs]
    assert "Hi, do you make round basins in charcoal?" in qs
    delivery = next(p for p in pairs if "delivery" in p["q"].lower())
    assert "[phone]" in delivery["q"] and "082 555 1234" not in delivery["q"]
    assert delivery["a"].startswith("Delivery in Cape Town is R350")
    assert any(p["a"].startswith("We take 50% upfront") for p in pairs)
    assert not any(p["q"] == "Thanks!" for p in pairs)                  # not a question


@pytest.mark.asyncio
async def test_learn_saves_samples_and_pending_answers_and_asks_when_unsure(monkeypatch):
    saved = {}
    monkeypatch.setattr(cl, "_hints", lambda tid: ["Matt Wilkinson"])
    monkeypatch.setattr(cl, "rewrite_pairs", AsyncMock(return_value=[
        {"question": "Do you deliver, and what does it cost?", "answer": "Delivery in Cape Town is R350."}]))
    monkeypatch.setattr(cl, "save_voice_samples", lambda t, s: saved.setdefault("samples", s) and len(s))
    monkeypatch.setattr(cl, "save_answers", lambda t, items: saved.setdefault("answers", items) and len(items))
    monkeypatch.setattr("vula.commerce.voice_profile.analyze_voice",
                        AsyncMock(return_value={"suggested": "Warm, short, says 'shout if you need anything'."}))
    out = await cl.learn("seedleme", [{"text": ANDROID}, {"name": "p.zip", "b64": _zip(IPHONE)}])
    assert out["business"] == "Matt Wilkinson" and out["chats"] == 2
    assert out["answers_to_review"] == 1 and out["tone_suggested"].startswith("Warm")
    assert saved["answers"][0]["answer"] == "Delivery in Cape Town is R350."

    monkeypatch.setattr(cl, "_hints", lambda tid: [])
    unsure = await cl.learn("seedleme", [{"text": ANDROID}])
    assert set(unsure["need_sender"]) == {"Thandi Mokoena", "Matt Wilkinson"}
    picked = await cl.learn("seedleme", [{"text": ANDROID}], me="Matt Wilkinson")
    assert picked["business"] == "Matt Wilkinson"
    assert (await cl.learn("seedleme", [{"text": "not a chat"}]))["error"]


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.op = db, name, "select"

    def select(self, *a): return self
    def eq(self, *a): return self
    def limit(self, *a): return self

    def insert(self, rows):
        self.op, self.rows = "insert", rows
        return self

    def execute(self):
        from types import SimpleNamespace
        if self.op == "insert":
            self.db.setdefault(self.name, []).extend(self.rows)
            return SimpleNamespace(data=self.rows)
        return SimpleNamespace(data=list(self.db.get(self.name, [])))


def test_answers_land_pending_and_repeats_are_skipped(monkeypatch):
    from types import SimpleNamespace
    db = {"vula_learned_answers": [{"question": "How much is delivery?"}]}
    monkeypatch.setattr(cl, "_client", lambda: SimpleNamespace(table=lambda n: _Q(db, n)))
    n = cl.save_answers("seedleme", [
        {"question": "How much does delivery cost?", "answer": "R350 in Cape Town."},   # same as existing
        {"question": "Do you take a deposit?", "answer": "50% upfront on made to order."}])
    assert n == 1
    new = db["vula_learned_answers"][-1]
    assert new["status"] == "pending" and new["source"] == "chat_import"
    assert cl.save_voice_samples("seedleme", ["Morning! Yes we do."]) == 1
    assert cl.save_voice_samples("seedleme", ["Morning! Yes we do."]) == 0          # not twice
