"""
vula/commerce/promo.py — a picture promotion to the shop's customers on WhatsApp.

2026-10-13 (Ian, with Caught Online's broadcasts as the example: "modern, rich and fun"): the
owner says what's on offer — on WhatsApp ("promote the Round Drop-In Basin: 15% off this
weekend") or in Broadcast — and every customer who hasn't opted out gets one message: the
product's photo, "Hi Thandi! 15% off this weekend…", and a **Shop now** button that opens the
shop (click-tracked per customer). It goes out on the shop's approved promotion template
(wa_templates.promo_template), so it reaches customers outside WhatsApp's 24-hour window, through
the same broadcast path as every other campaign: audience, POPIA suppression, pacing and the
delivered/read/clicked counts.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

MAX_TEXT = 500


def clean(text: str) -> str:
    """One line — Meta refuses a template parameter with newlines, tabs or long runs of spaces."""
    return re.sub(r"\s+", " ", text or "").strip()[:MAX_TEXT]


def template_name(tenant_id: str, story: bool = False) -> str:
    from vula.commerce.job_config import template_name as _name
    from vula.commerce.wa_templates import PROMO_SUFFIX, STORY_SUFFIX
    return _name(tenant_id, STORY_SUFFIX if story else PROMO_SUFFIX)


async def template_status(tenant_id: str, story: bool = False) -> str:
    """APPROVED / PENDING / REJECTED / MISSING — the promotion can only go out once approved."""
    from vula.commerce import wa_templates
    name = template_name(tenant_id, story)
    for t in await wa_templates.list_templates(tenant_id):
        if t.get("name") == name:
            return t.get("status") or "UNKNOWN"
    return "MISSING"


# ── The business's own layout (2026-10-24, Off the Hook) ─────────────────────────────────────
# Staci's posts: a CAPS headline between emoji, a short description, a call to action. A draft is
# those three parts, each one line (the shop_story template holds the line breaks).
LIMITS = {"headline": 70, "body": 300, "cta": 120}
_WHEN_RE = re.compile(r"\b(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2}|monday|tuesday|wednesday|"
                      r"thursday|friday|saturday|sunday|tomorrow|tonight|today|this weekend|next week)\b",
                      re.IGNORECASE)


def _sentences(text: str) -> list:
    return [x for x in re.split(r"(?<=[.!?])\s+", text or "") if x.strip()]


def check_parts(parts: Dict[str, str], *, brief: str, trusted: str) -> Dict[str, str]:
    """Each part one line and within its limit; a sentence stating a price that isn't the
    owner's or the catalogue's, or a day/time the owner didn't give, is dropped."""
    from core.skills.base import unverified_prices
    out = {}
    for key, cap in LIMITS.items():
        kept = []
        for sent in _sentences(clean(parts.get(key) or "")):
            if unverified_prices(sent, [], set(), strict=True, also_trusted=f"{brief} {trusted}"):
                continue
            if any(m.group(0).lower() not in (brief or "").lower() for m in _WHEN_RE.finditer(sent)):
                continue
            kept.append(sent)
        text = " ".join(kept)
        out[key] = text if len(text) <= cap else text[:cap - 1].rstrip() + "…"
    return out


def _price_facts(product: Optional[Dict[str, Any]]) -> str:
    if not product:
        return ""
    cents = product.get("price_cents")
    price = f" R{int(cents) / 100:.2f}" if isinstance(cents, (int, float)) and cents else ""
    return f"{product.get('name') or ''}{price}"


async def draft(tenant_id: str, brief: str, product: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """{headline, body, cta} for the owner's brief, written like their own posts. The model only
    writes; prices, days and lengths are checked in code (check_parts)."""
    import json
    import litellm
    from core.llm_router import resolve_generation_route
    from core.prompt_safety import fence
    from vula.api.tenants import get_config
    from vula.commerce import promotions
    from vula.commerce.voice_profile import persona_part
    examples = promotions.examples(tenant_id, n=5)
    style = persona_part((get_config(tenant_id) or {}).get("persona_prompt") or "", "promotions")
    facts = _price_facts(product)
    prompt = (
        "Write a WhatsApp promotion for this shop, in exactly the style of its own past posts. "
        "Reply with JSON only: {\"headline\": one line, the shop's headline style (e.g. capitals "
        "between emoji), \"body\": one or two short sentences, \"cta\": one short call to "
        "action}. Use only facts from the owner's brief and the product facts — no prices, days, "
        "times or delivery promises that aren't there."
        + (f"\nTheir promotion style: {style}" if style else "")
        + fence("OWNER_BRIEF", brief)
        + (fence("PRODUCT_FACTS", facts) if facts else "")
        + "".join(fence(f"PAST_POST_{i}", promotions.mask_prices(e)[:600]) for i, e in enumerate(examples, 1))
    )
    litellm.drop_params = True
    model, api_key, api_base = await resolve_generation_route(task_type="promotion_draft")
    resp = await litellm.acompletion(model=model, messages=[{"role": "user", "content": prompt}],
                                     temperature=0.5, max_tokens=400, api_key=api_key, api_base=api_base)
    raw = re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.DOTALL)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        parts = json.loads(m.group(0)) if m else {}
    except ValueError:
        parts = {}
    parts = check_parts({k: str(parts.get(k) or "") for k in LIMITS}, brief=brief, trusted=facts)
    if not parts["headline"] or not parts["body"]:
        return {"error": "I couldn't write that promotion just now — please try again, or send me the "
                         "exact words you'd like."}
    return parts


async def pick(tenant_id: str, product_hint: Optional[str] = None,
               product_id: Optional[str] = None) -> Dict[str, Any]:
    """The photo and the page the button opens: the named product's, else the shop's front page
    with its first product photo. {"product", "image_url", "target_url"}."""
    from vula.commerce import web_basket
    products = await web_basket.shop_products(tenant_id)
    match = None
    if product_id:
        match = next((p for p in products if str(p.get("id")) == str(product_id)), None)
    elif product_hint:
        want = product_hint.strip().lower()
        named = [p for p in products if want in (p.get("name") or "").lower()]
        match = min(named, key=lambda p: len(p.get("name") or "")) if named else None
    photo_from = match or next((p for p in products if p.get("image_url")), None)
    return {"product": (match or {}).get("name"),
            "image_url": web_basket.sized((photo_from or {}).get("image_url"), 800),
            "target_url": web_basket.shop_url(tenant_id)}


def uninvited_count(tenant_id: str) -> int:
    """Imported contacts never invited to opt in — a promotion can't reach them."""
    try:
        from vula.commerce import service
        res = (service._client().table("commerce_contacts").select("id", count="exact")
               .eq("tenant_id", tenant_id).eq("consent_status", "unknown").limit(1).execute())
        return int(res.count or 0)
    except Exception:
        return 0


async def send(tenant_id: str, message: str, *, product_hint: Optional[str] = None,
               product_id: Optional[str] = None, image_url: Optional[str] = None,
               audience: Any = "all", test_phone: Optional[str] = None,
               dry_run: bool = True, parts: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Preview (dry_run) or send the promotion. Returns the broadcast result plus what was used.
    `parts` ({headline, body, cta}) goes out in the business's own layout (shop_story) once Meta
    has approved it; until then as one line on the older template."""
    story_status = "MISSING"
    if parts:
        story_status = await template_status(tenant_id, story=True)
        message = message or " ".join(parts.get(k) or "" for k in ("headline", "body", "cta"))
    text = clean(message)
    if not text:
        return {"error": "What's the offer? e.g. \"15% off all basins this weekend\"."}
    chosen = await pick(tenant_id, product_hint, product_id)
    if product_hint and not chosen["product"] and not product_id:
        return {"error": f"I couldn't find a product called '{product_hint}' in the shop."}
    image = (image_url or "").strip() or chosen["image_url"]
    if not image:
        return {"error": "The promotion needs a picture — add a product photo first."}
    story = bool(parts) and story_status == "APPROVED"
    status = story_status if story else await template_status(tenant_id)
    out = {"template": template_name(tenant_id, story), "template_status": status, "text": text,
           "story": story, "story_status": story_status if parts else None,
           "product": chosen["product"], "image_url": image, "target_url": chosen["target_url"],
           "ai_picture": "-ai-" in (image or "")}
    params = (["{name}", parts["headline"], parts["body"], parts.get("cta") or "-"] if story
              else ["{name}", text])
    if not dry_run and status != "APPROVED":
        return {**out, "error": (
            "The picture promotion template isn't approved by Meta yet "
            f"({status.lower()}) — Settings › WhatsApp templates › Submit standard templates, then "
            "it's usually approved within a day.")}
    from fastapi import HTTPException
    from vula.api.commerce import admin_send_broadcast
    try:
        res = await admin_send_broadcast(tenant_id, {
            "template_name": out["template"], "audience_filter": audience or "all",
            "name": f"Promotion: {text[:60]}", "header_image_url": image,
            "target_url": chosen["target_url"], "template_params": params,
            "test_phone": test_phone, "dry_run": dry_run})
    except HTTPException as exc:
        return {**out, "error": str(exc.detail)}
    return {**out, **res}
