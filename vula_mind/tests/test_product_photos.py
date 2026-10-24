"""Off the Hook, 24 Oct: real photos from the business's own posts as its website pictures.

44 of OTH's 45 website pictures were AI-made from the product name, and some don't look like OTH's
real fish. Staci's posts have real photos. A post's picture is now kept, matched to the product it
shows, checked (a real photo of the raw product, not a poster or a cooked dish) and offered to the
team — it becomes the website picture only after "Use photo".
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from vula.api import whatsapp as wa
from vula.commerce import promo_photos as pp

AI = "https://x.supabase.co/storage/v1/object/public/product-images/off-the-hook/products/1786-ai-hake.jpg"
REAL = "https://x.supabase.co/storage/v1/object/public/product-images/off-the-hook/products/1784-image.jpg"
PRODUCTS = [
    {"id": "sw", "name": "Swordfish Steaks (skinless & boneless)", "category": "fresh_fish", "image_url": AI},
    {"id": "kf", "name": "Kingklip Fillets", "category": "fresh_fish", "image_url": AI},
    {"id": "kc", "name": "Kingklip Centre Cuts (skinless)", "category": "fresh_fish", "image_url": AI},
    {"id": "hf", "name": "Hake Fillets", "category": "fresh_fish", "image_url": AI},
    {"id": "wc", "name": "Whole Chicken (Fresh)", "category": "fresh_chicken", "image_url": AI},
    {"id": "am", "name": "Atlantic Mackerel Fillets", "category": "frozen_seafood", "image_url": REAL},
]
SWORDFISH = ("**FRESH EXPORT-QUALITY SWORDFISH HAS ARRIVED!**\n\nPremium skinless & boneless "
             "swordfish loins. Order before 12pm for delivery tomorrow.")


def test_a_post_matches_the_product_it_is_about():
    assert pp.pick(PRODUCTS, SWORDFISH)["id"] == "sw"
    assert pp.pick(PRODUCTS, "🐟 THIS WEEK'S STAR: FRESH KINGKLIP FILLETS ⭐")["id"] == "kf"


def test_unclear_or_generic_posts_match_nothing():
    assert pp.pick(PRODUCTS, "FRESH KINGKLIP this week!") is None          # fillets or centre cuts?
    assert pp.pick(PRODUCTS, "FRESH STOCK MUST GO TODAY! 10% off all fresh fillets") is None
    # ...but what the photo shows can settle it
    assert pp.pick(PRODUCTS, "FRESH KINGKLIP this week!", "raw kingklip fillets")["id"] == "kf"


def test_a_product_with_a_real_photo_is_never_offered():
    assert not pp.needs_photo(PRODUCTS[-1]) and pp.needs_photo(PRODUCTS[0])
    assert pp.needs_photo({"image_url": None})


@pytest.mark.asyncio
@pytest.mark.parametrize("seen", [
    {"raw_product_photo": False, "mostly_text": False, "shows": "grilled swordfish on a plate"},
    {"raw_product_photo": True, "mostly_text": True, "shows": "swordfish sale poster"},
    None,                                                      # couldn't check → nothing offered
])
async def test_posters_cooked_dishes_and_unchecked_photos_are_not_offered(monkeypatch, seen):
    monkeypatch.setattr("vula.commerce.service.list_products", AsyncMock(return_value=PRODUCTS))
    monkeypatch.setattr(pp, "look", AsyncMock(return_value=seen))
    assert await pp.match("off-the-hook", SWORDFISH, b"jpg") is None


@pytest.mark.asyncio
async def test_a_real_raw_photo_is_matched(monkeypatch):
    monkeypatch.setattr("vula.commerce.service.list_products", AsyncMock(return_value=PRODUCTS))
    monkeypatch.setattr(pp, "look", AsyncMock(return_value={
        "raw_product_photo": True, "mostly_text": False, "shows": "raw swordfish loins"}))
    m = await pp.match("off-the-hook", SWORDFISH, b"jpg")
    assert m["product"]["id"] == "sw"


@pytest.mark.asyncio
async def test_still_ai_lists_fish_first(monkeypatch):
    monkeypatch.setattr("vula.commerce.service.list_products",
                        AsyncMock(return_value=list(reversed(PRODUCTS))))
    names = await pp.still_ai("off-the-hook")
    assert names[-1] == "Whole Chicken (Fresh)" and "Atlantic Mackerel Fillets" not in names


# ── forwarded posts keep their picture and offer it ──────────────────────────

@pytest.mark.asyncio
async def test_a_forwarded_post_picture_is_kept_and_offered(monkeypatch):
    wa._PROMO_BURSTS.clear()
    monkeypatch.setattr(wa, "_PROMO_ACK_DELAY_S", 0.01)
    monkeypatch.setattr(wa, "_download_media_bytes", AsyncMock(return_value=b"jpg"))
    monkeypatch.setattr(wa, "_upload_to_storage", lambda b, path, d, ct: f"https://s/{b}/{path}")
    saved = []
    monkeypatch.setattr("vula.commerce.promotions.save",
                        lambda t, body, mid, mime, by, url=None: saved.append((body, url)) or {"id": f"p{len(saved)}"})
    monkeypatch.setattr(pp, "match", AsyncMock(side_effect=[
        {"product": PRODUCTS[0], "shows": "raw swordfish"}, None]))
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(return_value=True))
    monkeypatch.setattr("vula.open_questions.ask", lambda *a, **k: "q")
    offered = AsyncMock()
    monkeypatch.setattr(wa, "_offer_product_photos", offered)
    monkeypatch.setattr(wa, "_propose_customer_voice", AsyncMock(return_value=False))
    await wa._save_forwarded_promo("off-the-hook", "27645755210", SWORDFISH, "media1", "image/jpeg")
    await wa._save_forwarded_promo("off-the-hook", "27645755210", "", "media2", "image/jpeg")
    import asyncio
    await asyncio.sleep(0.1)
    assert saved[0][0] == SWORDFISH and saved[0][1].startswith("https://s/product-images/off-the-hook/promotions/")
    assert saved[1][0] == "" and saved[1][1]                       # a picture with no words is kept
    (tid, phone, photos), _ = offered.call_args
    assert [m["product"]["id"] for m in photos] == ["sw"]


@pytest.mark.asyncio
async def test_the_offer_shows_the_photo_then_use_photo_or_skip(monkeypatch):
    monkeypatch.setattr(wa, "_get_tenant_wa_creds", AsyncMock(return_value={"phone_id": "1", "token": "t"}))
    img = AsyncMock(return_value=True)
    monkeypatch.setattr(wa, "_send_wa_image", img)
    monkeypatch.setattr(wa, "_staff_role", lambda t, p: None)
    asked = []
    monkeypatch.setattr(wa, "_ask_admin_confirm", AsyncMock(side_effect=lambda *a, **k: asked.append((a, k))))
    sent = []
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(side_effect=lambda p, m, *a, **k: sent.append(m) or True))
    monkeypatch.setattr(pp, "still_ai", AsyncMock(return_value=["Swordfish Steaks (skinless & boneless)",
                                                                "Hake Fillets", "Kingklip Fillets"]))
    await wa._offer_product_photos("off-the-hook", "27645755210",
                                   [{"product": PRODUCTS[0], "image_url": "https://s/p.jpg"}])
    assert img.call_args.args[2] == "https://s/p.jpg"
    (args, kw), = asked
    assert args[2] == "set_product_photo" and args[3]["product_id"] == "sw"
    assert kw["labels"] == ("Use photo", "Skip") and "now an AI picture" in kw["summary"]
    assert "Still AI pictures on your website: Hake Fillets, Kingklip Fillets." in sent[0]


# ── "photo Hake Fillets" from the team ───────────────────────────────────────

@pytest.mark.asyncio
async def test_a_captioned_team_photo_is_offered_for_that_product(monkeypatch):
    monkeypatch.setattr(wa, "_is_team", lambda t, p: True)
    monkeypatch.setattr("vula.commerce.service.list_products", AsyncMock(return_value=PRODUCTS))
    monkeypatch.setattr(wa, "_keep_post_picture", AsyncMock(return_value=("https://s/h.jpg", b"x")))
    offer = AsyncMock()
    monkeypatch.setattr(wa, "_offer_product_photo", offer)
    assert await wa._maybe_product_photo("off-the-hook", "27645755210", "m1", "photo Hake Fillets", "image/jpeg")
    assert offer.call_args.args[2]["id"] == "hf" and offer.call_args.args[3] == "https://s/h.jpg"
    assert not await wa._maybe_product_photo("off-the-hook", "27645755210", "m1", "2kg hake please", None)


@pytest.mark.asyncio
async def test_a_customer_photo_caption_does_nothing(monkeypatch):
    monkeypatch.setattr(wa, "_is_team", lambda t, p: False)
    assert not await wa._maybe_product_photo("off-the-hook", "27820001111", "m1", "photo Hake Fillets", None)


# ── Use photo ────────────────────────────────────────────────────────────────

class _Q:
    def __init__(self, store, log):
        self.store, self.log, self.mode = store, log, "select"

    def select(self, *a):
        self.mode = "select"
        return self

    def update(self, row):
        self.mode = "update"
        self.log.append(row)
        self.store.update(row)
        return self

    def eq(self, *a):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        return MagicMock(data=[dict(self.store)])


@pytest.mark.asyncio
async def test_use_photo_sets_the_cover_drops_ai_pictures_and_reads_back(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill, checked_reply
    store, log = {"id": "hf", "name": "Hake Fillets", "image_url": AI, "images": [AI, AI + "2", REAL]}, []
    db = MagicMock()
    db.table.side_effect = lambda n: _Q(store, log)
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    skill = CommerceAdminSkill()
    preview = await skill._set_product_photo("off-the-hook", {"product_id": "hf", "image_url": "https://s/h.jpg"})
    assert preview["preview"] and not log                         # nothing changes without the tap
    res = await skill._set_product_photo("off-the-hook", {"product_id": "hf", "image_url": "https://s/h.jpg",
                                                          "confirm": True})
    assert store["image_url"] == "https://s/h.jpg" and store["images"] == ["https://s/h.jpg", REAL]
    assert "Hake Fillets* now shows your own photo" in checked_reply(res)


def test_set_product_photo_is_preview_only_in_dry_runs():
    from core import dry_run
    assert "set_product_photo" in dry_run.PREVIEW_UNLESS_CONFIRMED
