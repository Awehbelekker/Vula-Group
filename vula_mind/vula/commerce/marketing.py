"""
vula/commerce/marketing.py — AI marketing copy for a tenant.

"Write today's specials post / a product description / promo copy / a broadcast blast."
Grounded in the tenant's REAL catalogue (never invents products or prices) and voiced for
their business. Local-first generation via the shared LLM router. Returns a couple of
variants so the owner can pick and tweak.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

KINDS = ("specials", "product", "promo", "broadcast")

_KIND_BRIEF = {
    "specials": (
        "Write a short, punchy 'today's specials / fresh today' post highlighting a few of the "
        "in-stock items below. Make people want to order now. Good for a WhatsApp status or a "
        "Facebook/Instagram caption."
    ),
    "product": (
        "Write an appetising product description for the specific product named in the topic. "
        "Cover what it is, why it's great, and a serving/use idea. 2–4 sentences."
    ),
    "promo": (
        "Write promotional copy for the offer/topic described. Create urgency and a clear call to "
        "action, but stay honest — don't promise anything not stated in the topic."
    ),
    "broadcast": (
        "Write a friendly WhatsApp broadcast message to existing customers about the topic. Short, "
        "personal, with a clear call to action. Must feel like a message from the shop owner, not an ad."
    ),
}


def _clean(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


async def _context(tenant_id: str) -> Dict[str, Any]:
    """Real business + catalogue context so copy is grounded, never fabricated."""
    from vula.commerce import service
    from vula.api import tenants as _tenants

    cfg = _tenants.get_config(tenant_id)
    name = cfg.get("display_name") or tenant_id
    biz = cfg.get("business_type") or "small business"

    products: List[dict] = []
    try:
        products = await service.list_products(tenant_id, in_stock_only=True)
    except Exception as exc:
        log.debug("marketing: product load skipped: %s", exc)

    def _line(p: dict) -> str:
        price = f"R{(p.get('price_cents') or 0) / 100:.2f}"
        unit = "/kg" if (p.get("sold_by") == "kg") else ""
        return f"{p['name']} ({price}{unit})"

    daily = [p for p in products if p.get("is_daily_catch")]
    return {
        "name": name, "business_type": biz,
        "catalog": [_line(p) for p in products[:40]],
        "daily_catch": [_line(p) for p in daily[:12]],
        "product_names": [p["name"] for p in products],
    }


def _find_product(topic: str, ctx: Dict[str, Any]) -> Optional[str]:
    t = (topic or "").lower().strip()
    if not t:
        return None
    for line in ctx["catalog"]:
        if t in line.lower():
            return line
    return None


async def generate(tenant_id: str, *, kind: str = "specials", topic: str = "",
                   tone: str = "", variants: int = 2) -> Dict[str, Any]:
    """Generate marketing copy. Returns {kind, variants:[...]} or {error}."""
    import litellm
    from core.llm_router import resolve_generation_route

    kind = kind if kind in KINDS else "specials"
    ctx = await _context(tenant_id)
    tone = (tone or "warm, friendly and genuinely South African").strip()
    n = max(1, min(int(variants or 2), 3))

    catalog_block = ", ".join(ctx["daily_catch"] or ctx["catalog"]) or "(catalogue not loaded)"
    focus = ""
    if kind == "product":
        matched = _find_product(topic, ctx)
        if not matched and not topic:
            return {"error": "Tell me which product to describe."}
        focus = f"\nProduct to describe: {matched or topic}\n"
    elif topic:
        focus = f"\nTopic / offer: {topic}\n"

    prompt = (
        f"You are the marketing voice for {ctx['name']}, a South African {ctx['business_type']}.\n"
        f"Tone: {tone}. Use ZAR (e.g. R120.00). Emojis are welcome but sparing.\n"
        f"NEVER invent products, prices or claims — only use what's given.\n\n"
        f"In-stock items you may reference:\n{catalog_block}\n"
        f"{focus}\n"
        f"Task: {_KIND_BRIEF[kind]}\n\n"
        f"Write {n} distinct option(s). Separate each option with a line containing only '---'. "
        f"Output ONLY the copy — no preamble, no numbering, no explanations."
    )

    litellm.drop_params = True
    model, api_key, api_base = await resolve_generation_route(task_type="marketing_copy")
    try:
        resp = await litellm.acompletion(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0.8, max_tokens=650, api_key=api_key, api_base=api_base,
        )
        text = _clean(resp.choices[0].message.content or "")
    except Exception as exc:
        log.warning("marketing generation failed: %s", exc)
        return {"error": "Could not generate copy right now — please try again."}

    parts = [p.strip() for p in re.split(r"\n-{3,}\n", text) if p.strip()]
    if not parts:
        parts = [text] if text else []
    if not parts:
        return {"error": "Empty response — please try again."}
    return {"kind": kind, "variants": parts[:n]}
