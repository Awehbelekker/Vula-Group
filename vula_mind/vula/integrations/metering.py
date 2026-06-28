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

logger = logging.getLogger(__name__)

# Set at request entry (chat / doc analysis) → read inside the litellm callback.
_current_tenant: contextvars.ContextVar = contextvars.ContextVar("vula_tenant", default=None)


def set_request_tenant(tenant_id: str) -> None:
    try:
        _current_tenant.set(tenant_id)
    except Exception:
        pass


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


def _success_callback(kwargs, completion_response, start_time, end_time) -> None:
    """litellm success callback — fires after every completion."""
    try:
        tid = _current_tenant.get()
        if not tid:
            return
        u = getattr(completion_response, "usage", None)
        pt = getattr(u, "prompt_tokens", 0) or 0
        ct = getattr(u, "completion_tokens", 0) or 0
        record_llm(tid, kwargs.get("model") or "", int(pt), int(ct))
    except Exception:
        pass


def install_metering() -> None:
    """Register the litellm success callback once (idempotent)."""
    try:
        import litellm
        cbs = litellm.success_callback or []
        if _success_callback not in cbs:
            litellm.success_callback = cbs + [_success_callback]
            logger.info("Vula metering: litellm success callback installed")
    except Exception as exc:
        logger.warning("metering install failed: %s", exc)


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
