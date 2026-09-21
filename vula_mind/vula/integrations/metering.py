"""
vula/integrations/metering.py — per-tenant COGS visibility.

Captures token usage from EVERY litellm call via a success callback, attributing it to the
current request's tenant through a contextvar (set at the request entry points). Estimated
cost uses a per-model price map. Also snapshots storage + vectors per tenant daily.
"""
from __future__ import annotations

import contextvars
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Set at request entry (chat / doc analysis) → read inside the litellm callback.
_current_tenant: contextvars.ContextVar = contextvars.ContextVar("vula_tenant", default=None)


def set_request_tenant(tenant_id: str) -> None:
    try:
        _current_tenant.set(tenant_id)
    except Exception:
        pass


def get_request_tenant() -> Optional[str]:
    """The tenant_id set_request_tenant() attached to the current request, if any — read by
    core/llm_router.py's spend-cap gate so it doesn't need tenant_id threaded through every
    resolve_generation_route() call site."""
    try:
        return _current_tenant.get()
    except Exception:
        return None


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


# USD per 1M tokens (input, output). ollama/* (local) is free.
_PRICES = {
    "meta-llama/llama-3.3-70b-instruct": (0.13, 0.40),
    "google/gemini-2.5-flash": (0.075, 0.30),
    "google/gemini-2.5-flash-lite": (0.04, 0.15),
    "google/gemini-2.5-pro": (1.25, 5.0),
}
_DEFAULT = (0.15, 0.45)


def _price(model: str):
    m = model or ""
    if m.startswith("ollama/"):
        return (0.0, 0.0)
    if m.startswith("openrouter/"):
        m = m[len("openrouter/"):]
    return _PRICES.get(m, _DEFAULT)


def record_llm(tenant_id: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
    if not tenant_id:
        return
    pin, pout = _price(model)
    cost = round((prompt_tokens / 1e6) * pin + (completion_tokens / 1e6) * pout, 6)
    short = (model or "").replace("openrouter/", "").replace("ollama/", "")
    day = datetime.now(timezone.utc).date().isoformat()
    try:
        db = _client()
        ex = (db.table("vula_ai_usage").select("id,calls,prompt_tokens,completion_tokens,est_cost_usd")
              .eq("tenant_id", tenant_id).eq("day", day).eq("model", short).limit(1).execute().data or [])
        if ex:
            e = ex[0]
            db.table("vula_ai_usage").update({
                "calls": (e["calls"] or 0) + 1,
                "prompt_tokens": (e["prompt_tokens"] or 0) + prompt_tokens,
                "completion_tokens": (e["completion_tokens"] or 0) + completion_tokens,
                "est_cost_usd": float(e["est_cost_usd"] or 0) + cost, "updated_at": "now()",
            }).eq("id", e["id"]).execute()
        else:
            db.table("vula_ai_usage").insert({
                "tenant_id": tenant_id, "day": day, "model": short, "calls": 1,
                "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                "est_cost_usd": cost}).execute()
    except Exception as exc:
        logger.debug("meter skipped (run migration 031?): %s", exc)


IMAGE_COST_USD = 0.04  # gemini-2.5-flash-image via OpenRouter, per generated image


def record_image(tenant_id: str, model: str, images: int = 1) -> None:
    """Meter AI image generation (fixed per-image cost, not token-based)."""
    if not tenant_id:
        return
    cost = round(IMAGE_COST_USD * images, 6)
    short = (model or "").replace("openrouter/", "") + ":image"
    day = datetime.now(timezone.utc).date().isoformat()
    try:
        db = _client()
        ex = (db.table("vula_ai_usage").select("id,calls,est_cost_usd")
              .eq("tenant_id", tenant_id).eq("day", day).eq("model", short).limit(1).execute().data or [])
        if ex:
            e = ex[0]
            db.table("vula_ai_usage").update({
                "calls": (e["calls"] or 0) + images,
                "est_cost_usd": float(e["est_cost_usd"] or 0) + cost, "updated_at": "now()",
            }).eq("id", e["id"]).execute()
        else:
            db.table("vula_ai_usage").insert({
                "tenant_id": tenant_id, "day": day, "model": short, "calls": images,
                "prompt_tokens": 0, "completion_tokens": 0, "est_cost_usd": cost}).execute()
    except Exception as exc:
        logger.debug("image meter skipped: %s", exc)


def meter_response(tenant_id: str, model: str, resp) -> None:
    """Record usage straight from a litellm response (reliable, same-context path)."""
    try:
        u = getattr(resp, "usage", None)
        pt = getattr(u, "prompt_tokens", 0) or 0
        ct = getattr(u, "completion_tokens", 0) or 0
        record_llm(tenant_id, model or "", int(pt), int(ct))
    except Exception:
        pass


_INSTALLED = False


def install_metering() -> None:
    """Register an async litellm CustomLogger once. Async callbacks run in the event loop,
    so the request's tenant contextvar is visible (unlike sync success_callback threads)."""
    global _INSTALLED
    if _INSTALLED:
        return
    try:
        import litellm
        from litellm.integrations.custom_logger import CustomLogger

        class _VulaMeter(CustomLogger):
            async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
                try:
                    # Capture EVERYTHING — attribute to the request tenant when known,
                    # else "_unattributed" so background/test spend is never invisible.
                    tid = _current_tenant.get() or "_unattributed"
                    u = getattr(response_obj, "usage", None)
                    pt = getattr(u, "prompt_tokens", 0) or 0
                    ct = getattr(u, "completion_tokens", 0) or 0
                    record_llm(tid, kwargs.get("model") or "", int(pt), int(ct))
                except Exception:
                    pass

        litellm.callbacks = (litellm.callbacks or []) + [_VulaMeter()]
        _INSTALLED = True
        logger.info("Vula metering: litellm CustomLogger installed")
    except Exception as exc:
        logger.warning("metering install failed: %s", exc)


# ── Spend cap (migration 166) ────────────────────────────────────────────────
# Opt-in, per-tenant daily cap. Fail-open throughout: any read error returns "not capped" /
# "no spend" rather than blocking generation over a metering hiccup — cost control must never
# become an availability bug.

def today_spend(tenant_id: str) -> float:
    """Sum of today's est_cost_usd across all models for this tenant."""
    if not tenant_id:
        return 0.0
    day = datetime.now(timezone.utc).date().isoformat()
    try:
        db = _client()
        rows = (db.table("vula_ai_usage").select("est_cost_usd")
                .eq("tenant_id", tenant_id).eq("day", day).execute().data or [])
        return sum(float(r.get("est_cost_usd") or 0) for r in rows)
    except Exception as exc:
        logger.debug("today_spend read skipped: %s", exc)
        return 0.0


def spend_cap_usd(tenant_id: str) -> Optional[float]:
    """This tenant's configured daily cap, or None if unset — capping is opt-in, no default cap
    unless an operator sets one (master_usage()/VulaMasterPanel.jsx)."""
    if not tenant_id:
        return None
    try:
        db = _client()
        rows = (db.table("vula_tenant_config").select("spend_cap_usd")
                .eq("tenant_id", tenant_id).limit(1).execute().data or [])
        cap = rows[0].get("spend_cap_usd") if rows else None
        return float(cap) if cap is not None else None
    except Exception as exc:
        logger.debug("spend_cap_usd read skipped: %s", exc)
        return None


def is_over_spend_cap(tenant_id: str) -> bool:
    """True only if this tenant HAS a cap set AND today's spend has reached it."""
    cap = spend_cap_usd(tenant_id)
    if cap is None:
        return False
    return today_spend(tenant_id) >= cap


async def snapshot_infra() -> int:
    """Daily snapshot of per-tenant vectors (Qdrant) + storage (Supabase) + est cost."""
    import os
    import httpx
    qbase = (os.environ.get("QDRANT_BASE") or "").rstrip("/")
    qkey = os.environ.get("QDRANT_API_KEY") or ""
    if not qbase:
        return 0
    day = datetime.now(timezone.utc).date().isoformat()
    n = 0
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            cols = (await client.get(f"{qbase}/collections", headers={"api-key": qkey})).json()
            for c in cols.get("result", {}).get("collections", []):
                name = c["name"]
                if not name.startswith("vula_") or name == "vula_vula_training":
                    continue
                tenant = name[len("vula_"):].replace("_", "-")
                info = (await client.get(f"{qbase}/collections/{name}", headers={"api-key": qkey})).json()
                vectors = info.get("result", {}).get("points_count", 0) or 0
                # ~6KB/vector (1536-dim + payload); Qdrant free <1GB, then ~$0.02/GB/mo.
                est = round((vectors * 6 / 1e6) * 0.02, 4)
                try:
                    _client().table("vula_infra_snapshot").upsert({
                        "tenant_id": tenant, "day": day, "vectors": vectors,
                        "est_cost_usd": est, "updated_at": "now()"},
                        on_conflict="tenant_id,day").execute()
                    n += 1
                except Exception as exc:
                    logger.debug("infra snapshot upsert skipped: %s", exc)
    except Exception as exc:
        logger.warning("snapshot_infra failed: %s", exc)
    return n
