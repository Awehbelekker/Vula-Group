"""
vula/commerce/page_copy.py — AI-drafted real copy for a storefront page-builder template.

Real motivation (Ian): tenants need to be able to make changes to their own site without Vula
being involved each time, and get a genuinely better result than a from-scratch manual attempt —
where a tenant without design skill ends up with generic/inconsistent copy. This module fixes the
STARTING POINT: a tenant edits/publishes from an already-good draft instead of fighting a blank
template into shape. Ongoing self-editing drift after that point is explicitly out of scope.

Takes a Puck `content` array (one of VulaPages.jsx's TEMPLATES, unmodified structurally — same
blocks, same ids, same live-data blocks) and asks the LLM to write REAL, business-specific copy
for the marketing-copy fields only. Structural props (links, image URLs, counts, category keys)
are never touched — merge always falls back to the template's own value field-by-field, so one
malformed field never fails the whole page.

Contact facts (ContactCard/WhatsAppCTA phone/email/address) are NEVER written by the LLM — they
are merged in afterward from the tenant's own commerce_invoice_settings, only if actually set,
same "real data overrides template placeholder" precedent VulaPages.jsx's homeSeed() already
established for Hero copy (extended here, not inverted: hero_tagline/hero_subtitle win over
AI-drafted Hero copy the same way).

Testimonials is deliberately excluded outright (see EXCLUDED_BLOCKS) — a customer quote
attributed to a name is a said-by-a-real-person claim, not marketing copy about the business.

On-demand, single LLM call per generation, triggered by an explicit dashboard action — no
background job, no auto-trigger. Unlike voice_profile.py there is NO minimum-sample gate: a
brand-new tenant with nothing but business_type + a company name still gets a usable first
draft; quality scales with more real grounding (KB content, hero_tagline, persona_prompt) but a
baseline draft is never blocked.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# type -> top-level props the AI may write. Everything else on that block (image, href, count,
# category, id, animation, ...) is structural and passed through untouched.
COPYABLE_FIELDS: Dict[str, List[str]] = {
    "Hero": ["title", "subtitle", "ctaText", "ctaText2"],
    "Heading": ["text"],
    "Text": ["text"],
    "CTA": ["text"],
    "Features": ["title"],
    "TwoColumns": ["leftHeading", "leftBody", "rightHeading", "rightBody"],
    "Gallery": ["title"],
    "WhatsAppCTA": ["message", "buttonText"],
    "ContactCard": ["title"],  # phone/email/address are real-data-only, see _apply_real_data_overrides
    "AnnouncementBar": ["text", "linkText"],
    "ProductGrid": ["title"],
    "FeaturedProducts": ["title"],
    "CategoryNav": ["title"],
    "Booking": ["title", "subtitle"],
    "FAQ": ["title"],
    "PricingTable": ["title"],  # price is never AI-written — same "never invent a fact/number" rule as contact info
}
# type -> {array prop name: item fields the AI may write}. Item COUNT is always fixed to
# whatever the template already has — the AI fills existing slots, never adds/removes items.
COPYABLE_ARRAY_FIELDS: Dict[str, Dict[str, List[str]]] = {
    "Features": {"items": ["heading", "body"]},
    "FAQ": {"items": ["question", "answer"]},
    "PricingTable": {"tiers": ["name", "ctaText"]},  # price/features bullets stay template-fixed, tenant edits manually
}
# Never sent to the LLM, never touched on merge — kept exactly as the template scaffolded it.
EXCLUDED_BLOCKS = {"Testimonials"}

_FIELD_HINTS = {
    "title": "punchy headline, ~3-8 words",
    "subtitle": "one short supporting sentence",
    "text": "1-3 sentences",
    "ctaText": "short button label, 2-4 words",
    "ctaText2": "short button label, 2-4 words",
    "leftHeading": "short heading, 2-5 words",
    "leftBody": "1-2 sentences",
    "rightHeading": "short heading, 2-5 words",
    "rightBody": "1-2 sentences",
    "message": "one casual sentence, first person, as if the owner typed it",
    "buttonText": "short button label with an emoji, matches existing style e.g. '💬 Order on WhatsApp'",
    "linkText": "2-4 words or empty",
    "heading": "1-3 words",
    "body": "one short sentence",
    "question": "a real, specific question a customer of this business might actually ask",
    "answer": "1-2 sentences, a real answer",
    "name": "short plan/tier name, 1-3 words",
}


def _clean(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


async def _grounding(tenant_id: str, description: str = "") -> Dict[str, Any]:
    """Everything real Vula already knows about this tenant, gathered defensively — each source
    is independent and best-effort; a missing one never blocks the others (unlike
    voice_profile.py, there is no MIN_SAMPLE gate here — a baseline draft always runs)."""
    from vula.commerce import service
    from vula.api import tenants as _tenants

    cfg = _tenants.get_config(tenant_id) or {}
    invoice: Dict[str, Any] = {}
    try:
        invoice = await service.get_invoice_settings(tenant_id) or {}
    except Exception as exc:
        log.debug("page_copy: invoice settings skipped: %s", exc)

    order_settings: Dict[str, Any] = {}
    try:
        rows = (service._client().table("commerce_order_settings").select("*")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        order_settings = rows[0] if rows else {}
    except Exception as exc:
        log.debug("page_copy: order settings skipped (run migration 084?): %s", exc)

    display_name = (cfg.get("display_name") or invoice.get("trading_as")
                     or invoice.get("company_name") or tenant_id)
    kb_chunks: List[dict] = []
    try:
        from vula.ingestion.pipeline import VulaIngestionPipeline
        q = description.strip() or f"What does {display_name} do, and what makes it distinctive to customers?"
        kb_chunks = await VulaIngestionPipeline(tenant_id=tenant_id).query(q, top_k=5, authoritative_only=True)
    except Exception as exc:
        log.debug("page_copy: KB retrieval skipped (no ingested content?): %s", exc)

    return {
        "business_type": cfg.get("business_type") or "small business",
        "display_name": display_name,
        "persona_prompt": (cfg.get("persona_prompt") or "").strip(),
        "hero_tagline": (order_settings.get("hero_tagline") or "").strip(),
        "hero_subtitle": (order_settings.get("hero_subtitle") or "").strip(),
        "company_phone": (invoice.get("company_phone") or "").strip(),
        "company_email": (invoice.get("company_email") or "").strip(),
        "address": (invoice.get("registered_address") or "").strip(),
        "kb_chunks": kb_chunks,
    }


def _serialize_schema(content: List[dict]) -> Dict[str, dict]:
    """Build the {block_id: {type, <field>: hint, ...}} shape shown to the LLM — only for
    blocks/fields it's actually allowed to write. Returns {} if nothing on this page is
    AI-fillable (e.g. a page made only of live ProductGrid/Testimonials blocks)."""
    schema: Dict[str, dict] = {}
    for block in content:
        btype = block.get("type")
        bid = (block.get("props") or {}).get("id")
        if not bid or btype in EXCLUDED_BLOCKS or btype not in COPYABLE_FIELDS:
            continue
        entry: Dict[str, Any] = {"type": btype}
        for field in COPYABLE_FIELDS[btype]:
            entry[field] = _FIELD_HINTS.get(field, "short marketing copy")
        for arr_field, item_fields in COPYABLE_ARRAY_FIELDS.get(btype, {}).items():
            items = (block.get("props") or {}).get(arr_field) or []
            entry[arr_field] = [
                {f: _FIELD_HINTS.get(f, "short copy") for f in item_fields} for _ in items
            ]
        schema[bid] = entry
    return schema


# --- Phase D: layout composition — the AI may OMIT (never add/reorder) a small allow-list of
# non-essential, non-live-data blocks when there's genuinely not enough real content to justify
# them, instead of always keeping the full fixed template regardless of what's actually known
# about the business. Capped and allow-list-filtered in _apply_omissions regardless of what the
# model requests, so a misbehaving response can't gut the page.
OMITTABLE_BLOCKS = {"Gallery", "TwoColumns", "AnnouncementBar"}
_MAX_OMIT_FRACTION = 0.3


def _build_prompt(grounding: Dict[str, Any], description: str, schema: Dict[str, dict],
                   omittable_ids: Optional[set] = None) -> str:
    lines = [
        f"You are writing real website copy for {grounding['display_name']}, a South African "
        f"{grounding['business_type']} business.",
    ]
    if grounding["persona_prompt"]:
        lines.append(f"Tone/voice: {grounding['persona_prompt']}")
    if description.strip():
        lines.append(f"The owner describes their business as: {description.strip()}")
    if grounding["kb_chunks"]:
        extracts = "\n".join(
            f"[{c.get('filename', 'doc')}]: {c.get('text', '')[:500]}" for c in grounding["kb_chunks"]
        )
        lines.append(
            ">>> Real extracts from this business's own documents/website — ground your copy in "
            f"these where relevant, do not contradict them:\n{extracts}\n<<<"
        )
    lines.append(DESIGN_PRINCIPLES)
    lines.append(
        "Below is the JSON shape of a website template, keyed by block id. For each block, write "
        "real copy for ONLY the fields listed, matching each field's hint for length/style. Keep "
        "any fixed-length arrays (e.g. 'items') at exactly the same number of entries shown. "
        "Never invent a customer name, quote, phone number, email address, or physical address — "
        "those are not your job and are handled separately. If you have too little information "
        "to write something specific and true, write good generic-but-professional copy for this "
        "business_type instead of guessing invented facts (e.g. never invent a founding year, "
        "number of staff, or awards).\n\n"
        f"Template shape:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
    )
    if omittable_ids:
        lines.append(
            "If, based on the information above, there genuinely isn't enough real content to "
            'justify one of these specific blocks, you may optionally add a top-level "omit": '
            f"[block_id, ...] listing which of these ids to drop: {sorted(omittable_ids)}. Only "
            "omit a block you're confident would look empty/generic — most pages should keep "
            "everything; omitting nothing is a perfectly good answer."
        )
    lines.append(
        "Return STRICT JSON only, the exact same shape (block id -> filled fields, optionally "
        'plus "omit"), no other text, no markdown fences, no preamble.'
    )
    return "\n\n".join(lines)


def _apply_omissions(content: List[dict], drafted: Dict[str, Any]) -> List[dict]:
    """Drop blocks the LLM recommended omitting — constrained to OMITTABLE_BLOCKS and capped at
    _MAX_OMIT_FRACTION of the page regardless of what the model actually requests."""
    requested = drafted.get("omit")
    if not isinstance(requested, list) or not requested:
        return content
    omittable_ids = {
        (b.get("props") or {}).get("id") for b in content if b.get("type") in OMITTABLE_BLOCKS
    }
    to_omit = [bid for bid in requested if isinstance(bid, str) and bid in omittable_ids]
    max_omit = max(0, int(len(content) * _MAX_OMIT_FRACTION))
    to_omit = set(to_omit[:max_omit])
    if not to_omit:
        return content
    return [b for b in content if (b.get("props") or {}).get("id") not in to_omit]


def _parse_llm_json(raw: str) -> Dict[str, Any]:
    raw = _clean(raw)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    i, j = raw.find("{"), raw.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        data = json.loads(raw[i:j + 1])
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _merge(content: List[dict], drafted: Dict[str, Any]) -> List[dict]:
    """Per-field defensive merge — a malformed/missing field just keeps the template's own
    value; never fails the whole page for one bad field."""
    out = []
    for block in content:
        btype = block.get("type")
        bid = (block.get("props") or {}).get("id")
        props = dict(block.get("props") or {})
        block_draft = drafted.get(bid) if isinstance(drafted.get(bid), dict) else {}

        for field in COPYABLE_FIELDS.get(btype, []):
            val = block_draft.get(field)
            if isinstance(val, str) and val.strip():
                props[field] = val.strip()

        for arr_field, item_fields in COPYABLE_ARRAY_FIELDS.get(btype, {}).items():
            template_items = props.get(arr_field) or []
            drafted_items = block_draft.get(arr_field)
            if isinstance(drafted_items, list):
                merged_items = []
                for idx, tpl_item in enumerate(template_items):
                    item = dict(tpl_item)
                    d_item = (drafted_items[idx] if idx < len(drafted_items)
                              and isinstance(drafted_items[idx], dict) else {})
                    for f in item_fields:
                        v = d_item.get(f)
                        if isinstance(v, str) and v.strip():
                            item[f] = v.strip()
                    merged_items.append(item)
                props[arr_field] = merged_items

        out.append({**block, "props": props})
    return out


def _apply_real_data_overrides(content: List[dict], grounding: Dict[str, Any]) -> List[dict]:
    """Contact facts and hero copy: ALWAYS from real tenant settings if set, NEVER from the LLM.
    Applied after the LLM merge so drafted text can never shadow real data — same precedence
    VulaPages.jsx's homeSeed() already applies for Hero, extended here to ContactCard/
    WhatsAppCTA and made unconditional (not just for the homepage seed)."""
    out = []
    for block in content:
        btype, props = block.get("type"), dict(block.get("props") or {})
        if btype == "Hero":
            if grounding["hero_tagline"]:
                props["title"] = grounding["hero_tagline"]
            if grounding["hero_subtitle"]:
                props["subtitle"] = grounding["hero_subtitle"]
        elif btype == "ContactCard":
            if grounding["company_phone"]:
                props["phone"] = grounding["company_phone"]
            if grounding["company_email"]:
                props["email"] = grounding["company_email"]
            if grounding["address"]:
                props["address"] = grounding["address"]
        elif btype == "WhatsAppCTA":
            if grounding["company_phone"]:
                props["phone"] = grounding["company_phone"]
        out.append({**block, "props": props})
    return out


# --- Phase B: theme suggestion (color/font/motion) — grounded in real design principles ---
#
# Deliberate accuracy choice: an LLM (vision or text) never supplies the exact color that
# reaches a tenant. Generating an arbitrary hex code is exactly the kind of open-ended claim a
# model gets subtly wrong (plausible-looking but not actually sampled/measured). Instead:
#   - Mood classification (which of a small, known set of looks this resembles) is the only
#     thing ever asked of a model — a constrained pick from ~4 options, which models are
#     reliably good at, unlike open-ended generation.
#   - The actual color is either a pre-vetted, pre-contrast-checked value from that mood's own
#     shortlist, or — when a reference image is involved — the shortlist color nearest to a
#     REAL, deterministically-computed dominant color (extract_dominant_color, pure pixel math,
#     zero LLM involvement).
#   - Any LLM-suggested exact hex is only ever used if it independently passes a real WCAG
#     contrast check; otherwise it's discarded in favor of the vetted fallback, never offered
#     anyway "in good faith."

# 2026-08-14: expanded from a few sentences on visual design alone to also cover copywriting,
# conversion, accessibility, and SEO — the fuller "design skill" a real page designer/copywriter
# would actually apply, not just color/font/motion taste. Shared verbatim across the draft,
# refine, and polish prompts below (_build_prompt/_build_refine_prompt/_HOLISTIC_POLISH_SYSTEM)
# so every AI touch-point benefits from the same knowledge, regardless of which entry point
# (dashboard click, refine-chat message, or a WhatsApp/commerce_admin tool call) triggered it.
DESIGN_PRINCIPLES = (
    "Copywriting: lead with a concrete benefit or outcome, not a vague claim ('Fresh fish "
    "delivered same-day' beats 'Quality you can trust'). A headline works best as a clear "
    "promise or a specific number/timeframe, not a clever pun. CTA button text names the actual "
    "next action ('Book a fitting', 'Get a quote'), not a generic 'Learn more' or 'Submit'. "
    "Write for the reader's situation, not the business's org chart — 'delivered to your door', "
    "not 'we operate a fleet of delivery vehicles'.\n"
    "Conversion: put the strongest real proof point (a real number, a real guarantee, a real "
    "specific detail) as close to the call to action as possible — proof right before an ask "
    "works better than proof buried mid-page. Build urgency from something real and specific "
    "('order before 10am for same-day delivery'), never from invented scarcity. Don't stack two "
    "sections making the same point in different words — each section should say something the "
    "others don't.\n"
    "Accessibility: write link/button text that makes sense read on its own, out of context "
    "(never 'click here'). Keep sentences short enough to scan on a phone screen — most tenants' "
    "traffic is mobile. (Color contrast itself is handled separately by this module's own "
    "computed contrast_ratio check, not by wording.)\n"
    "SEO: work the business's actual name, location, and what it sells naturally into headings "
    "and body copy — a real sentence a human would say, never a keyword list. Don't repeat the "
    "exact same phrase in every section; natural variation reads better and covers more real "
    "search terms.\n"
    "Design: pair the heading font deliberately with the chosen mood, not arbitrarily. Use a "
    "genuinely neutral, deliberately-chosen ink color, not a generic default. Avoid overused "
    "AI-generated-design clichés (cream+terracotta, purple-gradient hero, everything centered). "
    "Use motion selectively — not every block animated, skip motion on dense/informational "
    "blocks like ContactCard. Order content for narrative flow: hero, then proof/features, then "
    "trust, then a call to action."
)

# Each preset's accent_shortlist is pre-vetted: every color already passes the contrast check
# below against both white and the fixed ink color, so anything drawn from here is always safe
# to offer without a runtime check — the check exists for the one path that bypasses this
# shortlist (an LLM-suggested exact color, see suggest_theme).
MOOD_PRESETS: Dict[str, Dict[str, Any]] = {
    "minimal": {
        "label": "Minimal", "font_pairing": "modern",
        "accent_shortlist": ["#2C5545", "#1E1E1E", "#3D5A80"],
        "motion_intensity": "subtle",
    },
    "warm_earthy": {
        "label": "Warm & earthy", "font_pairing": "classic",
        "accent_shortlist": ["#A2632B", "#7A4A2B", "#B5763C"],
        "motion_intensity": "subtle",
    },
    "bold_energetic": {
        "label": "Bold & energetic", "font_pairing": "modern",
        "accent_shortlist": ["#C1440E", "#D62828", "#E07A00"],
        "motion_intensity": "expressive",
    },
    "classic_elegant": {
        "label": "Classic & elegant", "font_pairing": "editorial",
        "accent_shortlist": ["#1E1E1E", "#2C5545", "#4B2E39"],
        "motion_intensity": "subtle",
    },
    "playful": {
        # Distinct from bold_energetic (which reads urgent/warm) — bright coral/teal/violet,
        # genuinely playful rather than a call-to-action red. All 3 hand-verified via
        # contrast_ratio() against both white and _DEFAULT_INK before being added here.
        # Coral is listed first: nearest_preset_color() falls back to shortlist[0] as the
        # default accent whenever there's no reference image to match against, so this order
        # is what actually determines which color a tenant sees by default.
        "label": "Playful & fun", "font_pairing": "modern",
        "accent_shortlist": ["#D6456B", "#0E7C7B", "#6C4AB6"],
        "motion_intensity": "expressive",
    },
}
_DEFAULT_MOOD = "minimal"
_DEFAULT_INK = "#1E1E1E"  # matches VulaSettings.jsx's own default ink color

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _valid_hex(v: Any) -> bool:
    return isinstance(v, str) and bool(_HEX_RE.match(v))


def _hex_to_rgb(hexval: str):
    hexval = hexval.lstrip("#")
    return tuple(int(hexval[i:i + 2], 16) for i in (0, 2, 4))


def _color_distance(a: str, b: str) -> float:
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return ((ar - br) ** 2 + (ag - bg) ** 2 + (ab - bb) ** 2) ** 0.5


def _relative_luminance(hexval: str) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c / 255) for c in _hex_to_rgb(hexval))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """Real WCAG contrast ratio between two colors (1.0..21.0) — computed, not eyeballed."""
    la, lb = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def extract_dominant_color(img) -> Optional[str]:
    """Deterministic (non-LLM) dominant-color extraction from a PIL Image — plain pixel
    quantization, so the color used downstream is actually sampled from the image, not guessed
    by a vision model. Skips near-white/near-black/low-saturation pixels so a photo's incidental
    background/whitespace doesn't win over its real brand-relevant color. Returns None if the
    image has no clearly dominant non-neutral color (e.g. a greyscale image)."""
    from collections import Counter
    small = img.convert("RGB").resize((50, 50))
    counts: Counter = Counter()
    for r, g, b in small.getdata():
        mx, mn = max(r, g, b), min(r, g, b)
        if mx > 235 and mn > 200:   # near-white
            continue
        if mx < 30:                 # near-black
            continue
        if mx - mn < 20:            # low saturation / gray
            continue
        counts[(r // 16 * 16, g // 16 * 16, b // 16 * 16)] += 1
    if not counts:
        return None
    r, g, b = counts.most_common(1)[0][0]
    return f"#{r:02X}{g:02X}{b:02X}"


def nearest_preset_color(preset_key: str, target_hex: Optional[str]) -> str:
    """Whichever color in a mood preset's own vetted shortlist is closest to a real, computed
    target color — or the shortlist's first (default) entry if no usable target is given."""
    preset = MOOD_PRESETS.get(preset_key) or MOOD_PRESETS[_DEFAULT_MOOD]
    shortlist = preset["accent_shortlist"]
    if not _valid_hex(target_hex):
        return shortlist[0]
    return min(shortlist, key=lambda c: _color_distance(c, target_hex))


def suggest_theme(mood: Optional[str] = None, ai_accent: Optional[str] = None,
                   computed_dominant_hex: Optional[str] = None) -> Dict[str, Any]:
    """Resolve a full theme suggestion (accent/ink color, font pairing, motion intensity),
    always snapped to a pre-vetted, contrast-checked value — never a freely-generated color.

    mood: one of MOOD_PRESETS' keys — from the tenant's own preset pick, or a vision model's
      constrained mood classification. Falls back to the default preset if not recognized.
    ai_accent: an LLM-suggested exact hex, if any — only used if it independently clears a real
      contrast check against white and the fixed ink color; otherwise discarded, never offered
      "in good faith."
    computed_dominant_hex: a real, deterministically-computed dominant color (extract_dominant_
      color) — used to pick the closest-matching color from the mood's own vetted shortlist.
    """
    preset_key = mood if mood in MOOD_PRESETS else _DEFAULT_MOOD
    preset = MOOD_PRESETS[preset_key]

    accent = None
    if ai_accent and _valid_hex(ai_accent):
        if contrast_ratio(ai_accent, "#FFFFFF") >= 3.0 and contrast_ratio(ai_accent, _DEFAULT_INK) >= 1.5:
            accent = ai_accent
    if not accent:
        accent = nearest_preset_color(preset_key, computed_dominant_hex)

    return {
        "accent_color": accent,
        "ink_color": _DEFAULT_INK,
        "font_pairing": preset["font_pairing"],
        "motion_intensity": preset["motion_intensity"],
        "mood": preset_key,
    }


# Restrained, non-uniform animation cycles per intensity — deterministic (not LLM-decided) so
# motion assignment is always predictable and always follows the "not every block, skip motion
# on dense/informational blocks" design principle above.
_MOTION_CYCLES = {
    "subtle": ["fadeIn", "none", "fadeIn", "none"],
    "expressive": ["fadeUp", "scaleIn", "slideInLeft", "fadeUp", "slideInRight"],
}
_MOTION_SKIP_BLOCKS = {"ContactCard", "AnnouncementBar", "Spacer", "Divider"}
_ANIMATABLE_BLOCKS = {"Hero", "Heading", "Text", "ImageBlock", "CTA", "VideoEmbed", "WhatsAppCTA"}


DESIGN_REFERENCE_PROMPT = """You are a design analyst. Look at this reference image (a website
screenshot, photo, or mood board a small-business owner says they like) and classify its overall
style. Return ONLY a JSON object (no prose, no markdown fences):
{
  "mood": "minimal" | "warm_earthy" | "bold_energetic" | "classic_elegant" | "playful",
  "style_note": "<one short sentence describing the look>"
}
Pick whichever "mood" is the closest overall match — do not invent a new category, and do not
attempt to specify exact colors, hex codes, or fonts; only classify the mood. If genuinely
unsure, pick "minimal"."""


def clean_design_reference(raw: dict) -> dict:
    """Whitelist the vision model's output — only ever accepts a real MOOD_PRESETS key and a
    short note. Deliberately has no path for a color/hex field to survive even if a model
    returns one unprompted; the accent color always comes from extract_dominant_color +
    nearest_preset_color instead (see suggest_theme)."""
    out: Dict[str, Any] = {}
    mood = raw.get("mood")
    if isinstance(mood, str) and mood in MOOD_PRESETS:
        out["mood"] = mood
    note = raw.get("style_note")
    if isinstance(note, str) and note.strip():
        out["style_note"] = note.strip()[:200]
    return out


def assign_motion(content: List[dict], motion_intensity: str = "subtle") -> List[dict]:
    """Deterministically assign each animatable block's `animation` prop from a restrained cycle
    matching the resolved motion_intensity — never touches blocks without an animation field or
    ones design principle says should stay still (ContactCard etc)."""
    cycle = _MOTION_CYCLES.get(motion_intensity, _MOTION_CYCLES["subtle"])
    out, i = [], 0
    for block in content:
        btype = block.get("type")
        if btype not in _ANIMATABLE_BLOCKS or btype in _MOTION_SKIP_BLOCKS:
            out.append(block)
            continue
        props = dict(block.get("props") or {})
        props["animation"] = cycle[i % len(cycle)]
        i += 1
        out.append({**block, "props": props})
    return out


async def generate_page_copy(tenant_id: str, content: List[dict], description: str = "") -> Dict[str, Any]:
    """Fill in real, business-specific copy for a Puck content array's marketing-copy fields.
    Returns {"content": [...]} (same shape or shorter if blocks were omitted, see
    OMITTABLE_BLOCKS) or {"error": str}. Never raises."""
    if not isinstance(content, list) or not content:
        return {"error": "No page content to draft copy for."}

    grounding = await _grounding(tenant_id, description)
    schema = _serialize_schema(content)
    if not schema:
        return {"content": content}  # nothing AI-fillable on this page — not an error

    omittable_ids = {
        (b.get("props") or {}).get("id") for b in content
        if b.get("type") in OMITTABLE_BLOCKS and (b.get("props") or {}).get("id")
    }

    import litellm
    from core.llm_router import resolve_generation_route

    litellm.drop_params = True
    model, api_key, api_base = await resolve_generation_route(task_type="page_copy")
    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": _build_prompt(grounding, description, schema, omittable_ids)}],
            temperature=0.6, max_tokens=1200, api_key=api_key, api_base=api_base,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        log.warning("page_copy: generation failed: %s", exc)
        return {"error": "Could not draft page copy right now — please try again."}

    drafted = _parse_llm_json(raw)
    merged = _merge(content, drafted)
    merged = _apply_real_data_overrides(merged, grounding)
    merged = _apply_omissions(merged, drafted)
    return {"content": merged}


# --- Phase C: self-critique over the ASSEMBLED page ---
#
# Nothing until now reviews the composed result — copy and theme are each generated well in
# isolation, but nothing catches a Hero title that's too long and will wrap badly, a section
# that feels thin/empty, or button text that's low-contrast once actually placed on its real
# accent-colored background. No generate-then-critique-and-revise pattern existed anywhere in
# this codebase before this (confirmed) — core/verification.py's adversarial pass is the closest
# precedent in spirit (a "reviewer, not a generator" checker prompt) but only ever appends a
# caveat, never revises; this needs to produce fixes, so it's new code, following the same
# fail-open discipline (any error just returns the page unmodified).

# Hard problem thresholds — tighter than _FIELD_HINTS' generation guidance (a soft ask), these
# are a genuine display-overflow risk, not just "could be tighter."
_LENGTH_CEILINGS = {
    "title": 70, "subtitle": 140, "text": 280, "ctaText": 28, "ctaText2": 28,
    "leftHeading": 40, "rightHeading": 40, "leftBody": 220, "rightBody": 220,
    "message": 200, "buttonText": 32, "linkText": 24, "heading": 30, "body": 160,
}
# A field that's present but suspiciously short reads as thin/unfinished, not intentionally terse.
_LENGTH_FLOORS = {
    "subtitle": 8, "text": 8, "leftBody": 8, "rightBody": 8, "body": 4,
}
_ACCENT_TEXT_BLOCKS = {"CTA", "WhatsAppCTA"}  # button text renders white-on-accent by convention


def critique_deterministic(content: List[dict], theme: Optional[Dict[str, Any]] = None) -> List[dict]:
    """Cheap, computed checks over the assembled page — no LLM, mirrors extraction_quality.py's
    pass/fail-with-reasons style. `theme` is optional — the contrast check only runs when given
    (theme is resolved by the caller, separately from copy generation). Returns [] when nothing's
    wrong, which is the common case and means critique_and_fix is never called (cost discipline)."""
    issues: List[dict] = []
    accent = (theme or {}).get("accent_color")
    for block in content:
        btype = block.get("type")
        props = block.get("props") or {}
        bid = props.get("id")
        if not bid:
            continue
        for field, ceiling in _LENGTH_CEILINGS.items():
            val = props.get(field)
            if isinstance(val, str) and len(val) > ceiling:
                issues.append({"block_id": bid, "field": field,
                                "issue": f"too long ({len(val)} chars) — will likely wrap or overflow"})
        for field, floor in _LENGTH_FLOORS.items():
            val = props.get(field)
            if isinstance(val, str) and val.strip() and len(val.strip()) < floor:
                issues.append({"block_id": bid, "field": field, "issue": "too short — reads as thin/unfinished"})
        if btype in _ACCENT_TEXT_BLOCKS and accent and contrast_ratio(accent, "#FFFFFF") < 3.0:
            issues.append({"block_id": bid, "field": "(background)",
                            "issue": "button text will be low-contrast against the accent color"})
    return issues


_CRITIQUE_SYSTEM = (
    "You are a design reviewer, not a generator. You will be shown a full drafted webpage (as "
    "JSON, block id -> current field values) and a list of automatically-detected issues. For "
    "each issue — and any other genuine problem you notice, such as a thin/empty-feeling section "
    "or two back-to-back sections saying the same thing — return a fix. Only return fields you "
    "are ACTUALLY changing; do not rewrite anything that isn't broken, and never make a fix "
    "longer than the field's original text. Return STRICT JSON only: "
    '{"fixes": {block_id: {field: new_value}}}'
)


async def _run_polish_call(content: List[dict], issues: List[dict], system_prompt: str) -> List[dict]:
    """Shared LLM-call plumbing for both critique_and_fix (named issues) and _holistic_polish
    (no named issues, general quality pass) — same serialize/call/parse/merge shape, only the
    system prompt and whether `issues` is empty differ. Fail-open: any error (timeout, bad JSON)
    returns content completely unmodified."""
    current = {
        bid: {f: v for f, v in props.items() if isinstance(v, str)}
        for block in content
        for props in [block.get("props") or {}]
        for bid in [props.get("id")]
        if bid
    }
    if not current:
        return content

    import litellm
    from core.llm_router import resolve_generation_route

    litellm.drop_params = True
    try:
        model, api_key, api_base = await resolve_generation_route(task_type="page_copy")
        resp = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps({"page": current, "issues": issues}, ensure_ascii=False)},
            ],
            temperature=0.3, max_tokens=800, api_key=api_key, api_base=api_base,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        log.debug("page_copy: polish pass failed, keeping draft as-is: %s", exc)
        return content

    fixes = _parse_llm_json(raw).get("fixes")
    if not isinstance(fixes, dict):
        return content
    return _merge(content, fixes)


async def critique_and_fix(content: List[dict], issues: List[dict]) -> List[dict]:
    """Runs only when critique_deterministic found something — most drafts don't, so most drafts
    never pay for this extra LLM call on its own. (See polish_page() for the "always refine"
    flow, which calls this as its bounded second pass — this function's own skip-when-empty
    behavior is unchanged and still used standalone.)"""
    if not issues:
        return content
    return await _run_polish_call(content, issues, _CRITIQUE_SYSTEM)


_HOLISTIC_POLISH_SYSTEM = (
    "You are a design reviewer doing a final quality pass, not a generator. You will be shown a "
    "full drafted webpage (as JSON, block id -> current field values). Look for anything that "
    "feels generic, thin, or like a placeholder rather than something specific to this real "
    "business — copy that could describe any business, a section with barely any content, two "
    "back-to-back sections saying the same thing. Improve what you find using ONLY real details "
    "already present in the page itself — never invent a new fact, name, quote, phone number, "
    "email, or address. Only return fields you are ACTUALLY changing; do not rewrite anything "
    "that's already good, and never make a fix longer than the field's original text.\n\n"
    + DESIGN_PRINCIPLES + "\n\n"
    'If genuinely nothing needs improving, return {"fixes": {}}. Return STRICT JSON only: '
    '{"fixes": {block_id: {field: new_value}}}'
)


async def _holistic_polish(content: List[dict]) -> List[dict]:
    """Unlike critique_and_fix, this ALWAYS calls the LLM once — no early return when there are
    no named issues. This is the "always refine" mandatory pass (see polish_page)."""
    return await _run_polish_call(content, [], _HOLISTIC_POLISH_SYSTEM)


async def polish_page(content: List[dict], theme: Optional[Dict[str, Any]] = None) -> List[dict]:
    """"Always refine, want it polished at the end" (Ian) — one MANDATORY holistic polish pass,
    then at most one more targeted fix pass if real problems remain after that. Hard-capped at 2
    total LLM polish calls regardless of outcome — bounded iteration, never an unbounded "keep
    refining" loop (no clear stopping criterion otherwise, and repeated rewrites risk drift/
    hallucination creeping in the more times content gets touched).

    Called on every AI-drafted or AI-refined page, not just the first draft — see
    admin_ai_draft_page_copy and refine_page_copy's callers."""
    polished = await _holistic_polish(content)
    issues = critique_deterministic(polished, theme)
    if issues:
        polished = await critique_and_fix(polished, issues)
    return polished


# --- Phase E: conversational refine — adjust the CURRENT draft per a specific instruction,
# instead of one-shot generate-and-review. Reuses the same safety machinery as
# generate_page_copy (contact facts real-data-only, Testimonials excluded, per-field defensive
# merge) — only the prompt framing and what gets serialized (current values, not hints) differ.

def _serialize_current(content: List[dict]) -> Dict[str, dict]:
    """Like _serialize_schema but shows CURRENT VALUES instead of generation hints, so the model
    edits real text instead of filling blanks."""
    out: Dict[str, dict] = {}
    for block in content:
        btype = block.get("type")
        props = block.get("props") or {}
        bid = props.get("id")
        if not bid or btype in EXCLUDED_BLOCKS or btype not in COPYABLE_FIELDS:
            continue
        entry: Dict[str, Any] = {}
        for field in COPYABLE_FIELDS[btype]:
            entry[field] = props.get(field, "")
        for arr_field, item_fields in COPYABLE_ARRAY_FIELDS.get(btype, {}).items():
            items = props.get(arr_field) or []
            entry[arr_field] = [{f: it.get(f, "") for f in item_fields} for it in items]
        out[bid] = entry
    return out


def _build_refine_prompt(grounding: Dict[str, Any], instruction: str, current: Dict[str, dict]) -> str:
    lines = [
        f"You are editing the existing website copy for {grounding['display_name']}, a South "
        f"African {grounding['business_type']} business, at the owner's specific request.",
    ]
    if grounding["persona_prompt"]:
        lines.append(f"Tone/voice: {grounding['persona_prompt']}")
    lines.append(DESIGN_PRINCIPLES)
    lines.append(
        f'The owner\'s request: "{instruction.strip()}"\n\n'
        "Below is the current copy for each block, keyed by block id. Apply the request. Only "
        "change fields the request actually implies — return every OTHER field completely "
        "unchanged (copy it through exactly as shown). Never invent a customer name, quote, "
        "phone number, email address, or physical address.\n\n"
        f"Current copy:\n{json.dumps(current, ensure_ascii=False)}\n\n"
        "Return STRICT JSON only, the exact same shape (block id -> fields), no other text, no "
        "markdown fences, no preamble."
    )
    return "\n\n".join(lines)


async def refine_page_copy(tenant_id: str, content: List[dict], instruction: str,
                            description: str = "") -> Dict[str, Any]:
    """Adjust the CURRENT draft (already AI-generated or manually edited) per a specific
    free-text instruction — e.g. "make the headline punchier". Same safety guarantees as
    generate_page_copy: contact facts real-data-only, Testimonials never touched, per-field
    defensive merge. Never raises."""
    if not isinstance(content, list) or not content:
        return {"error": "No page content to refine."}
    if not instruction or not instruction.strip():
        return {"error": "Tell me what you'd like to change."}

    grounding = await _grounding(tenant_id, description)
    current = _serialize_current(content)
    if not current:
        return {"content": content}

    import litellm
    from core.llm_router import resolve_generation_route

    litellm.drop_params = True
    model, api_key, api_base = await resolve_generation_route(task_type="page_copy")
    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": _build_refine_prompt(grounding, instruction, current)}],
            temperature=0.5, max_tokens=1200, api_key=api_key, api_base=api_base,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        log.warning("page_copy: refine failed: %s", exc)
        return {"error": "Could not apply that change right now — please try again."}

    drafted = _parse_llm_json(raw)
    merged = _merge(content, drafted)
    merged = _apply_real_data_overrides(merged, grounding)
    # "Always refine" (Ian) — every AI touch-point gets the same bounded polish pass, not just
    # the initial draft. No theme available at this layer (refine only ever changes copy, not
    # theme), so the contrast check inside polish_page's deterministic pass is skipped here —
    # already covered by the original draft's own polish pass.
    merged = await polish_page(merged)
    return {"content": merged}


# --- Reference-URL-driven block additions: Booking (live, backend-wired), FAQ, PricingTable ---
#
# Booking is wired to the ALREADY-BUILT bookings backend (vula/api/bookings.py, migration 050,
# mounted publicly at /v1/bookings — not behind tenant_admin_guard, confirmed intentional per its
# own docstring: "Powers dashboard scheduling, storefront 'book now', and WhatsApp") — a real
# public API, not a new exposure. FAQ/PricingTable are content-only, no backend needed, same
# shape discipline as the existing Features/Testimonials blocks.

FEATURE_BLOCK_MAP: Dict[str, str] = {
    "booking": "Booking",
    "faq": "FAQ",
    "pricing": "PricingTable",
}
# Features reference_url.py's vocabulary recognizes but Vula's block library/backend doesn't
# support yet — surfaced honestly to the tenant rather than silently ignored or half-built.
UNSUPPORTED_FEATURES = {"blog", "login", "live_chat", "newsletter_signup"}

_BLOCK_SCAFFOLDS: Dict[str, dict] = {
    "Booking": {"title": "Book an appointment", "subtitle": "Pick a service and a time that works for you."},
    "FAQ": {"title": "Frequently asked questions", "items": [
        {"question": "Question one", "answer": "Answer one."},
        {"question": "Question two", "answer": "Answer two."},
        {"question": "Question three", "answer": "Answer three."},
    ]},
    "PricingTable": {"title": "Pricing", "tiers": [
        {"name": "Basic", "price": "R0", "ctaText": "Get started",
         "features": [{"value": "Feature one"}, {"value": "Feature two"}]},
        {"name": "Standard", "price": "R0", "ctaText": "Get started",
         "features": [{"value": "Feature one"}, {"value": "Feature two"}, {"value": "Feature three"}]},
    ]},
}


def add_block(content: List[dict], block_type: str) -> List[dict]:
    """Append a new block instance from its fixed scaffold, with a fresh stable id — used when a
    tenant confirms a feature suggested by reference-URL analysis (see FEATURE_BLOCK_MAP). An
    unrecognized block_type is a no-op — never silently corrupts content."""
    scaffold = _BLOCK_SCAFFOLDS.get(block_type)
    if not scaffold:
        return content
    existing_ids = {(b.get("props") or {}).get("id") for b in content}
    n = 1
    new_id = f"added-{block_type.lower()}-{n}"
    while new_id in existing_ids:
        n += 1
        new_id = f"added-{block_type.lower()}-{n}"
    return content + [{"type": block_type, "props": {"id": new_id, **scaffold}}]
