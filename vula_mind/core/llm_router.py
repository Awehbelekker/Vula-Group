"""
core/llm_router.py — Local-first LLM routing with cloud fallback.

Decides which provider to use for *generation*:
  1. If local Ollama is reachable → use Ollama (local-first).
  2. Else if OPENROUTER_API_KEY is set → fall back to OpenRouter (hybrid).
  3. Else → return the Ollama route anyway so the caller fails loudly.

A cheap health probe (short timeout, cached) gives true automatic
failover without probing on every request.

Embeddings are deliberately NOT routed here: a Qdrant collection has a
fixed vector size, and local bge-m3 (1024-dim) and OpenRouter
text-embedding-3-small (1536-dim) are incompatible. Embeddings stay
pinned per-collection in the ingestion pipeline.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

import httpx

from config import settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Health-probe cache: base_url -> (checked_at_monotonic, is_up)
_HEALTH_TTL_S = 30.0
_PROBE_TIMEOUT_S = 1.5
_cache: dict[str, tuple[float, bool]] = {}


async def ollama_available(base: Optional[str] = None) -> bool:
    """Return True if the local Ollama endpoint answers within the timeout.

    The result is cached for _HEALTH_TTL_S so we don't probe on every request.
    """
    base = (base or settings.ollama_base).rstrip("/")
    now = time.monotonic()
    cached = _cache.get(base)
    if cached and (now - cached[0]) < _HEALTH_TTL_S:
        return cached[1]

    up = False
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            resp = await client.get(f"{base}/api/tags")
            up = resp.status_code == 200
    except Exception as exc:
        logger.debug("Ollama health probe failed for %s: %s", base, exc)
        up = False

    _cache[base] = (now, up)
    return up


def reset_health_cache() -> None:
    """Clear the health-probe cache (primarily for tests)."""
    _cache.clear()


async def resolve_generation_route(
    model: Optional[str] = None,
) -> Tuple[str, Optional[str], str]:
    """Local-first generation route with OpenRouter fallback.

    Returns (litellm_model, api_key, api_base).
    """
    # Local model name (Ollama) and cloud fallback model name (OpenRouter) may
    # differ — e.g. "deepseek-r1:8b" locally vs "meta-llama/llama-3.3-70b" on
    # OpenRouter. A caller-supplied `model` overrides the local name only.
    local_model = model or settings.model_worker
    cloud_model = settings.model_worker_cloud or settings.model_worker

    # Dev/test mode — force the cheapest model so QA doesn't burn premium tokens.
    import os
    if os.environ.get("VULA_DEV_MODE", "").lower() in ("1", "true", "yes") and settings.openrouter_api_key:
        return f"openrouter/{settings.model_worker_cheap}", settings.openrouter_api_key, OPENROUTER_BASE

    # Accuracy-first: force the smart cloud model regardless of local availability.
    if settings.prefer_cloud_llm and settings.openrouter_api_key:
        return f"openrouter/{cloud_model}", settings.openrouter_api_key, OPENROUTER_BASE

    if await ollama_available():
        return f"ollama/{local_model}", None, settings.ollama_base

    if settings.openrouter_api_key:
        logger.info("Ollama unreachable — falling back to OpenRouter (%s)", cloud_model)
        return f"openrouter/{cloud_model}", settings.openrouter_api_key, OPENROUTER_BASE

    logger.warning(
        "Ollama unreachable and no OPENROUTER_API_KEY set — generation will likely fail"
    )
    return f"ollama/{local_model}", None, settings.ollama_base


async def resolve_cheap_route(model: Optional[str] = None) -> Tuple[str, Optional[str], str]:
    """Cheap tier for mechanical work (doc analysis, classification, extraction).

    Cloud cheap model (gemini-flash, ~1/15th of the 70B), then the 70B as a last resort.
    The local GPU tunnel is intentionally NOT used (it sits behind Cloudflare bot-protection
    and is world-open); revisit once it's secured with a service token. Callers should
    validate the output and escalate to `resolve_cloud_route()` on failure/low confidence.
    """
    if settings.openrouter_api_key:
        return f"openrouter/{settings.model_worker_cheap}", settings.openrouter_api_key, OPENROUTER_BASE
    return f"openrouter/{settings.model_worker_cloud}", settings.openrouter_api_key, OPENROUTER_BASE


def resolve_cloud_route() -> Optional[Tuple[str, Optional[str], str]]:
    """Force the strong 70B cloud model — for user-facing answers and cheap-tier escalation."""
    if settings.openrouter_api_key:
        return f"openrouter/{settings.model_worker_cloud}", settings.openrouter_api_key, OPENROUTER_BASE
    return None


async def resolve_vision_route() -> Tuple[str, Optional[str], str]:
    """Local-first vision route with OpenRouter fallback (for the Smart Scanner).

    Unlike generation, the local and cloud tiers use *different* models: local
    vision is settings.model_ocr (e.g. llava), while the OpenRouter fallback uses
    settings.model_vision (e.g. a Claude vision model).

    Returns (litellm_model, api_key, api_base).
    """
    if await ollama_available():
        return f"ollama/{settings.model_ocr}", None, settings.ollama_base

    if settings.openrouter_api_key:
        logger.info("Ollama unreachable — falling back to OpenRouter for vision")
        return f"openrouter/{settings.model_vision}", settings.openrouter_api_key, OPENROUTER_BASE

    logger.warning(
        "Ollama unreachable and no OPENROUTER_API_KEY set — vision scan will likely fail"
    )
    return f"ollama/{settings.model_ocr}", None, settings.ollama_base


def resolve_cloud_vision_route() -> Optional[Tuple[str, Optional[str], str]]:
    """Force the cloud vision route regardless of Ollama availability.

    Used by the Smart Scanner to escalate a weak local read to the stronger
    cloud model (settings.model_vision). Returns None if no cloud key is set.
    """
    if settings.openrouter_api_key:
        return f"openrouter/{settings.model_vision}", settings.openrouter_api_key, OPENROUTER_BASE
    return None
