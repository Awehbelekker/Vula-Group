"""Tests for AI-drafted page copy (vula/commerce/page_copy.py).

Mirrors tests/test_voice_profile.py's style: a minimal fake Supabase client, mocked LLM call,
no real DB/model needed. Focus is on the guarantees that matter for trust, not LLM quality:
contact facts are never invented, Testimonials is never touched, malformed LLM output degrades
gracefully to the template's own defaults, and real settings always win over AI-drafted text.
"""
import pytest

import vula.commerce.page_copy as pc


TID = "test-tenant"

SAMPLE_CONTENT = [
    {"type": "Hero", "props": {"id": "t-hero", "title": "Fresh, local, delivered",
                                "subtitle": "Order before 10am.", "image": "",
                                "ctaText": "Shop now", "ctaHref": "/shop"}},
    {"type": "ContactCard", "props": {"id": "t-contact", "title": "Get in touch",
                                       "phone": "", "email": "", "address": "", "hours": ""}},
    {"type": "WhatsAppCTA", "props": {"id": "t-wa", "phone": "",
                                       "message": "Hi! I'd like to order.",
                                       "buttonText": "💬 Order on WhatsApp"}},
    {"type": "Testimonials", "props": {"id": "t-test", "title": "What customers say",
                                        "items": [{"quote": "Always fresh.",
                                                    "author": "A happy customer", "role": ""}]}},
]


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_kw):
        return self

    def eq(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _FakeClient:
    def __init__(self, order_settings_rows=None):
        self._order_settings_rows = order_settings_rows or []

    def table(self, name):
        if name == "commerce_order_settings":
            return _FakeQuery(list(self._order_settings_rows))
        return _FakeQuery([])


def _mock_llm(monkeypatch, content):
    async def _fake_route(*a, **kw):
        return ("fake-model", None, None)
    monkeypatch.setattr("core.llm_router.resolve_generation_route", _fake_route)

    class _Msg:
        pass
    _Msg.content = content

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    async def _fake_completion(*a, **kw):
        return _Resp()
    monkeypatch.setattr("litellm.acompletion", _fake_completion)


@pytest.fixture
def no_grounding(monkeypatch):
    """The brand-new-tenant case: no config, no invoice settings, no order settings, no KB."""
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, fresh=False: {})

    async def _no_invoice(tid):
        return {}
    monkeypatch.setattr("vula.commerce.service.get_invoice_settings", _no_invoice)
    monkeypatch.setattr("vula.commerce.service._client", lambda: _FakeClient())

    async def _no_kb(self, *a, **kw):
        return []
    monkeypatch.setattr("vula.ingestion.pipeline.VulaIngestionPipeline.query", _no_kb)


@pytest.mark.asyncio
async def test_never_invents_contact_facts_even_if_llm_returns_them(monkeypatch, no_grounding):
    # LLM (mis)behaving and returning a phone/email anyway must never survive the merge.
    _mock_llm(monkeypatch, '{"t-contact": {"title": "Reach out", "phone": "0821234567", "email": "fake@x.com"}}')
    result = await pc.generate_page_copy(TID, SAMPLE_CONTENT, description="")
    assert "error" not in result
    contact = next(b for b in result["content"] if b["props"]["id"] == "t-contact")
    assert contact["props"]["phone"] == ""   # not set in settings -> stays blank, LLM's value discarded
    assert contact["props"]["email"] == ""
    assert contact["props"]["title"] == "Reach out"  # non-contact-fact field is fine to accept


@pytest.mark.asyncio
async def test_testimonials_never_touched(monkeypatch, no_grounding):
    _mock_llm(monkeypatch, '{"t-test": {"title": "New title", "items": [{"quote": "Made up quote!", "author": "John Smith"}]}}')
    result = await pc.generate_page_copy(TID, SAMPLE_CONTENT, description="")
    testimonials = next(b for b in result["content"] if b["props"]["id"] == "t-test")
    assert testimonials["props"]["title"] == "What customers say"          # untouched
    assert testimonials["props"]["items"][0]["quote"] == "Always fresh."   # untouched
    assert testimonials["props"]["items"][0]["author"] == "A happy customer"


@pytest.mark.asyncio
async def test_malformed_json_degrades_gracefully(monkeypatch, no_grounding):
    _mock_llm(monkeypatch, "not json at all, sorry")
    result = await pc.generate_page_copy(TID, SAMPLE_CONTENT, description="")
    assert "error" not in result
    hero = next(b for b in result["content"] if b["props"]["id"] == "t-hero")
    assert hero["props"]["title"] == "Fresh, local, delivered"  # kept template default


@pytest.mark.asyncio
async def test_one_bad_field_does_not_fail_whole_page(monkeypatch, no_grounding):
    # subtitle is an int (malformed), title is fine -> subtitle keeps template default, title updates.
    _mock_llm(monkeypatch, '{"t-hero": {"title": "Real headline for this shop", "subtitle": 12345}}')
    result = await pc.generate_page_copy(TID, SAMPLE_CONTENT, description="")
    hero = next(b for b in result["content"] if b["props"]["id"] == "t-hero")
    assert hero["props"]["title"] == "Real headline for this shop"
    assert hero["props"]["subtitle"] == "Order before 10am."  # kept template default


@pytest.mark.asyncio
async def test_hero_tagline_override_respected(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, fresh=False: {"business_type": "food"})

    async def _no_invoice(tid):
        return {}
    monkeypatch.setattr("vula.commerce.service.get_invoice_settings", _no_invoice)
    monkeypatch.setattr(
        "vula.commerce.service._client",
        lambda: _FakeClient(order_settings_rows=[{"hero_tagline": "Real owner-set headline", "hero_subtitle": ""}]),
    )

    async def _no_kb(self, *a, **kw):
        return []
    monkeypatch.setattr("vula.ingestion.pipeline.VulaIngestionPipeline.query", _no_kb)
    _mock_llm(monkeypatch, '{"t-hero": {"title": "AI headline that should NOT win"}}')

    result = await pc.generate_page_copy(TID, SAMPLE_CONTENT, description="")
    hero = next(b for b in result["content"] if b["props"]["id"] == "t-hero")
    assert hero["props"]["title"] == "Real owner-set headline"   # real setting wins, not the LLM's


@pytest.mark.asyncio
async def test_no_content_is_an_error(no_grounding):
    result = await pc.generate_page_copy(TID, [], description="")
    assert "error" in result


@pytest.mark.asyncio
async def test_nothing_ai_fillable_returns_content_unchanged(monkeypatch, no_grounding):
    # A page made only of live/excluded blocks — not an error, just a no-op (and no LLM call
    # needed for it, though _mock_llm isn't even installed here to prove that).
    content = [{"type": "Testimonials", "props": {"id": "t1", "title": "x", "items": []}}]
    result = await pc.generate_page_copy(TID, content, description="")
    assert result["content"] == content


# --- Phase B: theme suggestion (color/font/motion) ---

def test_suggest_theme_defaults_to_preset_shortlist_color():
    theme = pc.suggest_theme(mood="warm_earthy")
    assert theme["mood"] == "warm_earthy"
    assert theme["accent_color"] in pc.MOOD_PRESETS["warm_earthy"]["accent_shortlist"]
    assert theme["font_pairing"] == pc.MOOD_PRESETS["warm_earthy"]["font_pairing"]
    assert theme["motion_intensity"] == pc.MOOD_PRESETS["warm_earthy"]["motion_intensity"]


def test_suggest_theme_unknown_mood_falls_back_to_default():
    theme = pc.suggest_theme(mood="not-a-real-preset")
    assert theme["mood"] == pc._DEFAULT_MOOD


def test_suggest_theme_rejects_low_contrast_ai_accent():
    # A near-white "accent" would be invisible on a white page — must be discarded, not offered.
    theme = pc.suggest_theme(mood="minimal", ai_accent="#FEFEFE")
    assert theme["accent_color"] != "#FEFEFE"
    assert theme["accent_color"] in pc.MOOD_PRESETS["minimal"]["accent_shortlist"]


def test_suggest_theme_accepts_high_contrast_ai_accent():
    # A legitimately high-contrast accent (even one not in the shortlist) should be allowed —
    # the check is about contrast, not shortlist membership.
    theme = pc.suggest_theme(mood="minimal", ai_accent="#8B0000")
    assert theme["accent_color"] == "#8B0000"


def test_suggest_theme_snaps_to_nearest_shortlist_color_from_computed_dominant():
    warm_shortlist = pc.MOOD_PRESETS["warm_earthy"]["accent_shortlist"]
    # A dominant color close to one specific shortlist entry should snap to THAT entry, not
    # just the first one in the list.
    target = warm_shortlist[-1]
    theme = pc.suggest_theme(mood="warm_earthy", computed_dominant_hex=target)
    assert theme["accent_color"] == target


def test_reference_image_path_never_lets_a_vision_hex_survive():
    # Even if a misbehaving vision model returns a "color"/"accent_color" field, the whitelist
    # cleaner has no path for it to survive — only mood/style_note are ever extracted.
    raw = {"mood": "bold_energetic", "color": "#FF00FF", "accent_color": "#00FF00",
           "style_note": "Bright and playful"}
    cleaned = pc.clean_design_reference(raw)
    assert cleaned == {"mood": "bold_energetic", "style_note": "Bright and playful"}
    assert "color" not in cleaned and "accent_color" not in cleaned

    theme = pc.suggest_theme(mood=cleaned["mood"], computed_dominant_hex="#1A2B3C")
    assert theme["accent_color"] in pc.MOOD_PRESETS["bold_energetic"]["accent_shortlist"]
    # The vision model's smuggled colors never appear anywhere in the final theme.
    assert theme["accent_color"] not in ("#FF00FF", "#00FF00")


def test_clean_design_reference_rejects_unknown_mood():
    cleaned = pc.clean_design_reference({"mood": "cyberpunk-neon", "style_note": "n/a"})
    assert "mood" not in cleaned


def test_extract_dominant_color_from_solid_image():
    from PIL import Image
    img = Image.new("RGB", (20, 20), (200, 60, 40))  # a clear, saturated orange-red
    hexval = pc.extract_dominant_color(img)
    assert hexval is not None
    r, g, b = pc._hex_to_rgb(hexval)
    assert r > g and r > b  # dominant channel preserved


def test_extract_dominant_color_returns_none_for_greyscale():
    from PIL import Image
    img = Image.new("RGB", (20, 20), (128, 128, 128))  # pure gray, no saturation
    assert pc.extract_dominant_color(img) is None


def test_assign_motion_skips_dense_blocks():
    content = [
        {"type": "Hero", "props": {"id": "h", "animation": "none"}},
        {"type": "ContactCard", "props": {"id": "c", "phone": ""}},
    ]
    out = pc.assign_motion(content, "expressive")
    hero = next(b for b in out if b["props"]["id"] == "h")
    contact = next(b for b in out if b["props"]["id"] == "c")
    assert hero["props"]["animation"] in pc._MOTION_CYCLES["expressive"]
    assert "animation" not in contact["props"]  # never touched


@pytest.mark.asyncio
async def test_ai_draft_endpoint_mood_path_needs_no_llm_call(monkeypatch, no_grounding):
    # The mood-preset-only path (no free-text/image) is a deterministic lookup — proving it
    # here at the page_copy level: suggest_theme + assign_motion never touch litellm at all.
    import litellm as _litellm

    async def _boom(*a, **kw):
        raise AssertionError("litellm.acompletion should not be called for the mood-only path")
    monkeypatch.setattr(_litellm, "acompletion", _boom)

    theme = pc.suggest_theme(mood="classic_elegant")
    content = pc.assign_motion(SAMPLE_CONTENT, theme["motion_intensity"])
    assert theme["mood"] == "classic_elegant"
    assert len(content) == len(SAMPLE_CONTENT)


def test_new_playful_preset_is_real_and_contrast_checked():
    assert "playful" in pc.MOOD_PRESETS
    theme = pc.suggest_theme(mood="playful")
    assert theme["mood"] == "playful"
    for hexval in pc.MOOD_PRESETS["playful"]["accent_shortlist"]:
        assert pc.contrast_ratio(hexval, "#FFFFFF") >= 3.0
        assert pc.contrast_ratio(hexval, pc._DEFAULT_INK) >= 1.5


# --- Phase C: self-critique over the assembled page ---

def test_critique_deterministic_flags_too_long_title():
    content = [{"type": "Hero", "props": {"id": "h", "title": "X" * 80, "subtitle": "fine"}}]
    issues = pc.critique_deterministic(content)
    assert any(i["block_id"] == "h" and i["field"] == "title" for i in issues)


def test_critique_deterministic_flags_thin_section():
    content = [{"type": "Text", "props": {"id": "t", "text": "hi"}}]
    issues = pc.critique_deterministic(content)
    assert any(i["block_id"] == "t" and i["field"] == "text" for i in issues)


def test_critique_deterministic_flags_low_contrast_button():
    content = [{"type": "CTA", "props": {"id": "c", "text": "Shop now"}}]
    theme = {"accent_color": "#FEFEFE"}  # near-white — invisible white-on-white button text
    issues = pc.critique_deterministic(content, theme)
    assert any(i["block_id"] == "c" for i in issues)


def test_critique_deterministic_clean_page_has_no_issues():
    content = [
        {"type": "Hero", "props": {"id": "h", "title": "Fresh, local, delivered", "subtitle": "Order before 10am."}},
        {"type": "CTA", "props": {"id": "c", "text": "Shop now"}},
    ]
    theme = {"accent_color": "#2C5545"}
    assert pc.critique_deterministic(content, theme) == []


@pytest.mark.asyncio
async def test_critique_and_fix_skips_llm_when_no_issues(monkeypatch):
    async def _boom(*a, **kw):
        raise AssertionError("litellm.acompletion should not be called when there are no issues")
    monkeypatch.setattr("litellm.acompletion", _boom)
    content = [{"type": "Hero", "props": {"id": "h", "title": "Fine"}}]
    assert await pc.critique_and_fix(content, []) == content


@pytest.mark.asyncio
async def test_critique_and_fix_applies_llm_fix(monkeypatch):
    content = [{"type": "Hero", "props": {"id": "h", "title": "X" * 80, "subtitle": "fine"}}]
    issues = pc.critique_deterministic(content)
    _mock_llm(monkeypatch, '{"fixes": {"h": {"title": "A shorter, punchier headline"}}}')
    fixed = await pc.critique_and_fix(content, issues)
    hero = next(b for b in fixed if b["props"]["id"] == "h")
    assert hero["props"]["title"] == "A shorter, punchier headline"
    assert hero["props"]["subtitle"] == "fine"  # untouched field preserved


@pytest.mark.asyncio
async def test_critique_and_fix_fails_open_on_llm_error(monkeypatch):
    async def _raise(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr("core.llm_router.resolve_generation_route", _raise)
    content = [{"type": "Hero", "props": {"id": "h", "title": "X" * 80}}]
    issues = pc.critique_deterministic(content)
    result = await pc.critique_and_fix(content, issues)
    assert result == content  # unmodified, not an error/crash


# --- Phase D: layout composition (block omission) ---

OMIT_CONTENT = [
    {"type": "Hero", "props": {"id": "hero", "title": "x"}},
    {"type": "Gallery", "props": {"id": "gal", "title": "x", "images": []}},
    {"type": "TwoColumns", "props": {"id": "two", "leftHeading": "x"}},
    {"type": "AnnouncementBar", "props": {"id": "ann", "text": "x"}},
]


def test_omission_respects_allowlist_even_if_llm_requests_more():
    # "hero" is requested but not in OMITTABLE_BLOCKS — must never be dropped.
    drafted = {"omit": ["hero", "gal"]}
    out = pc._apply_omissions(OMIT_CONTENT, drafted)
    ids = {(b["props"]["id"]) for b in out}
    assert "hero" in ids
    assert "gal" not in ids


def test_omission_capped_at_max_fraction():
    # 4 blocks, cap = int(4*0.3) = 1 — even if all 3 omittable ids are requested, only 1 goes.
    drafted = {"omit": ["gal", "two", "ann"]}
    out = pc._apply_omissions(OMIT_CONTENT, drafted)
    assert len(out) == len(OMIT_CONTENT) - 1


def test_omission_noop_when_not_requested():
    assert pc._apply_omissions(OMIT_CONTENT, {}) == OMIT_CONTENT


# --- Phase E: conversational refine ---

@pytest.mark.asyncio
async def test_refine_only_changes_what_instruction_implies(monkeypatch, no_grounding):
    _mock_llm(monkeypatch, '{"t-hero": {"title": "A punchier headline"}}')
    result = await pc.refine_page_copy(TID, SAMPLE_CONTENT, "make the headline punchier")
    assert "error" not in result
    hero = next(b for b in result["content"] if b["props"]["id"] == "t-hero")
    assert hero["props"]["title"] == "A punchier headline"
    assert hero["props"]["subtitle"] == "Order before 10am."  # untouched, preserved from current state


@pytest.mark.asyncio
async def test_refine_still_protects_contact_facts_and_testimonials(monkeypatch, no_grounding):
    _mock_llm(monkeypatch, (
        '{"t-contact": {"phone": "0821234567"}, '
        '"t-test": {"items": [{"quote": "Fabricated!", "author": "Nobody"}]}}'
    ))
    result = await pc.refine_page_copy(TID, SAMPLE_CONTENT, "add a phone number and a review")
    contact = next(b for b in result["content"] if b["props"]["id"] == "t-contact")
    testimonials = next(b for b in result["content"] if b["props"]["id"] == "t-test")
    assert contact["props"]["phone"] == ""  # no real setting -> LLM's value discarded
    assert testimonials["props"]["items"][0]["quote"] == "Always fresh."  # never touched


@pytest.mark.asyncio
async def test_refine_requires_instruction():
    result = await pc.refine_page_copy(TID, SAMPLE_CONTENT, "")
    assert "error" in result


@pytest.mark.asyncio
async def test_refine_requires_content():
    result = await pc.refine_page_copy(TID, [], "make it punchier")
    assert "error" in result


# --- "Always refine" — mandatory, bounded polish pass ---

def _mock_llm_sequence(monkeypatch, responses):
    """Like _mock_llm but returns each response in order across successive calls (repeats the
    last one if there are more calls than responses) — needed to test polish_page's multi-call
    behavior, unlike _mock_llm's single fixed response."""
    async def _fake_route(*a, **kw):
        return ("fake-model", None, None)
    monkeypatch.setattr("core.llm_router.resolve_generation_route", _fake_route)

    state = {"n": 0}

    async def _fake_completion(*a, **kw):
        idx = min(state["n"], len(responses) - 1)
        state["n"] += 1
        class _Msg:
            pass
        _Msg.content = responses[idx]
        class _Choice:
            message = _Msg()
        class _Resp:
            choices = [_Choice()]
        return _Resp()
    monkeypatch.setattr("litellm.acompletion", _fake_completion)
    return state


@pytest.mark.asyncio
async def test_polish_page_always_runs_holistic_pass_even_on_clean_content(monkeypatch):
    # Unlike critique_and_fix (skips the LLM when there are no named issues), polish_page's
    # holistic pass is mandatory — this is the actual "always refine" behavior Ian asked for.
    content = [{"type": "Hero", "props": {"id": "h", "title": "Fresh, local, delivered", "subtitle": "Order before 10am."}}]
    state = _mock_llm_sequence(monkeypatch, ['{"fixes": {}}'])
    result = await pc.polish_page(content)
    assert state["n"] == 1  # holistic pass ran despite nothing being deterministically wrong
    assert result == content  # no-op fixes -> content unchanged


@pytest.mark.asyncio
async def test_polish_page_runs_second_pass_when_issue_remains_after_holistic(monkeypatch):
    content = [{"type": "Hero", "props": {"id": "h", "title": "X" * 80}}]
    state = _mock_llm_sequence(monkeypatch, [
        '{"fixes": {}}',                                          # holistic pass: no-op
        '{"fixes": {"h": {"title": "A shorter headline"}}}',      # targeted fix pass: fixes it
    ])
    result = await pc.polish_page(content)
    assert state["n"] == 2
    hero = next(b for b in result if b["props"]["id"] == "h")
    assert hero["props"]["title"] == "A shorter headline"


@pytest.mark.asyncio
async def test_polish_page_hard_capped_at_two_calls(monkeypatch):
    # Every response is a no-op, so the title never actually gets fixed — proves the loop stops
    # at 2 total LLM calls regardless of outcome, not an unbounded "keep refining" loop.
    content = [{"type": "Hero", "props": {"id": "h", "title": "X" * 80}}]
    state = _mock_llm_sequence(monkeypatch, ['{"fixes": {}}'])
    result = await pc.polish_page(content)
    assert state["n"] == 2
    hero = next(b for b in result if b["props"]["id"] == "h")
    assert len(hero["props"]["title"]) == 80  # capped, not silently "resolved"


@pytest.mark.asyncio
async def test_refine_page_copy_also_gets_polished(monkeypatch, no_grounding):
    # refine_page_copy's output goes through polish_page too — "always refine" applies to every
    # AI touch-point, not just the initial draft.
    state = _mock_llm_sequence(monkeypatch, [
        '{"t-hero": {"title": "A punchier headline"}}',   # the refine call itself
        '{"fixes": {}}',                                   # polish_page's mandatory holistic pass
    ])
    result = await pc.refine_page_copy(TID, SAMPLE_CONTENT, "make the headline punchier")
    assert state["n"] == 2
    hero = next(b for b in result["content"] if b["props"]["id"] == "t-hero")
    assert hero["props"]["title"] == "A punchier headline"


# --- Reference-URL-driven block additions ---

def test_add_block_booking_appends_with_scaffold():
    out = pc.add_block(SAMPLE_CONTENT, "Booking")
    assert len(out) == len(SAMPLE_CONTENT) + 1
    added = out[-1]
    assert added["type"] == "Booking"
    assert added["props"]["title"]
    assert added["props"]["id"]


def test_add_block_faq_and_pricing_have_seeded_items():
    faq = pc.add_block([], "FAQ")[0]
    assert len(faq["props"]["items"]) == 3
    pricing = pc.add_block([], "PricingTable")[0]
    assert len(pricing["props"]["tiers"]) == 2
    assert pricing["props"]["tiers"][0]["price"]  # template default present, never AI-touched


def test_add_block_unknown_type_is_noop():
    assert pc.add_block(SAMPLE_CONTENT, "NotARealBlock") == SAMPLE_CONTENT


def test_add_block_generates_unique_ids_on_repeat_add():
    content = pc.add_block([], "FAQ")
    content = pc.add_block(content, "FAQ")
    ids = [b["props"]["id"] for b in content]
    assert len(ids) == len(set(ids))  # no collision


def test_price_is_never_in_pricing_table_copyable_fields():
    # Same discipline as contact facts — never let the AI invent a number.
    assert "price" not in pc.COPYABLE_ARRAY_FIELDS["PricingTable"]["tiers"]
