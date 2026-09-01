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

import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

import httpx

from config import settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Task types that genuinely need frontier (cloud) reasoning — the explicit part of the
# requirement-(c) complexity threshold. Everything else stays local unless the token cap trips.
# "verification" added 2026-08-22: a real transcript showed core.verification's adversarial
# checker (called with task_type="verification", local-first before this fix) timing out at its
# 8s cap on every single call for one tenant's whole session (5/5 checker_error) — the shared
# local Ollama box was too slow under load. Fails open by design, so nothing errored — it just
# silently never checked anything, letting a fabricated "invoice created" claim straight through
# with no caveat. Low-volume (one short, ~300-token check per already-agentic reply) and needs
# to be fast/reliable far more than it needs to be free — same reasoning commerce_admin already
# applies to its own tool-calling completions.
_FRONTIER_TASK_TYPES = {"architecture_planning", "standards_reasoning", "page_copy", "verification"}

# Health-probe cache: base_url -> (checked_at_monotonic, is_up)
_HEALTH_TTL_S = 30.0
_PROBE_TIMEOUT_S = 1.5
_cache: dict[str, tuple[float, bool]] = {}


def _host(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return (urlparse(url).netloc or url).lower()
    except Exception:
        return (url or "").lower()


def _ollama_headers() -> dict:
    """Cloudflare Access service-token headers for the local Ollama tunnel, from env.

    The tunnel (ollama.vula-ai.com) sits behind Cloudflare Access — every request must present
    these or it's blocked at the edge (POPIA: keeps the SA inference node private). Empty when
    unconfigured (e.g. a bare localhost Ollama), so local dev needs no token.
    """
    cid = os.environ.get("OLLAMA_CF_ACCESS_CLIENT_ID", "")
    csec = os.environ.get("OLLAMA_CF_ACCESS_CLIENT_SECRET", "")
    return {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": csec} if cid and csec else {}


_ollama_auth_installed = False


def install_ollama_auth() -> None:
    """Wrap litellm.acompletion once so calls to the Ollama base carry the CF-Access headers.

    Scoped by host — the service token is attached ONLY to requests hitting the configured Ollama
    base, never to OpenRouter. No-op if creds aren't set. Called lazily from resolve_generation_route
    so importing this module stays cheap (no eager litellm import)."""
    global _ollama_auth_installed
    if _ollama_auth_installed:
        return
    try:
        import litellm
    except Exception:
        return
    _orig = litellm.acompletion

    async def _wrapped(*args, **kwargs):
        hdrs = _ollama_headers()
        base = kwargs.get("api_base") or ""
        if hdrs and base and _host(base) == _host(settings.ollama_base):
            merged = dict(kwargs.get("extra_headers") or {})
            merged.update(hdrs)
            kwargs["extra_headers"] = merged
        return await _orig(*args, **kwargs)

    litellm.acompletion = _wrapped
    _ollama_auth_installed = True


async def ollama_available(base: Optional[str] = None, model: Optional[str] = None) -> bool:
    """Return True if the local Ollama endpoint answers within the timeout.

    When `model` is given, also require that model to be present in /api/tags — so a route to a
    model the local host hasn't pulled (e.g. the tunnel serves llama3.2:3b but MODEL_WORKER is
    llama3.1:8b) fails the probe and escalates-with-reason instead of 404ing silently.

    The result is cached per (base, model) for _HEALTH_TTL_S so we don't probe on every request.
    """
    base = (base or settings.ollama_base).rstrip("/")
    cache_key = f"{base}::{model or '*'}"
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and (now - cached[0]) < _HEALTH_TTL_S:
        return cached[1]

    up = False
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            resp = await client.get(f"{base}/api/tags", headers=_ollama_headers() or None)
            if resp.status_code == 200:
                if model:
                    names = {m.get("name") for m in (resp.json().get("models") or [])}
                    base_names = {(n or "").split(":")[0] for n in names}
                    up = model in names or model.split(":")[0] in base_names
                else:
                    up = True
    except Exception as exc:
        logger.debug("Ollama health probe failed for %s: %s", base, exc)
        up = False

    _cache[cache_key] = (now, up)
    return up


def reset_health_cache() -> None:
    """Clear the health-probe cache (primarily for tests)."""
    _cache.clear()


def assess_complexity(messages: Optional[list] = None, task_type: Optional[str] = None) -> Optional[str]:
    """Requirement (c): return a reason string if the task genuinely needs frontier (cloud)
    reasoning, else None. Explicit threshold: a frontier task_type, or an estimated prompt size
    (chars/4 ≈ tokens) at/above settings.local_complexity_token_cap (default 8000). Deliberately
    conservative — the majority of tenant traffic stays local."""
    if task_type and task_type in _FRONTIER_TASK_TYPES:
        return f"complexity:{task_type}"
    if messages:
        cap = int(getattr(settings, "local_complexity_token_cap", 8000) or 8000)
        chars = sum(len(str(m.get("content") or "")) for m in messages if isinstance(m, dict))
        if chars // 4 >= cap:
            return f"complexity:tokens>={cap}"
    return None


def compute_confidence(resp) -> Optional[float]:
    """Extract a 0.0-1.0 confidence estimate from a completion response's per-token
    logprobs, if the backend returned any. Ported from LocalCoder's
    ollama_backend._compute_confidence: mean log-probability of the generated tokens,
    converted to a mean probability via exp() (log-probs are <= 0, so this is bounded
    to (0.0, 1.0]).

    2026-08 audit finding: this scoring existed in looks_unreliable() but was dead —
    no caller ever passed a confidence value, because no local completion call
    requested logprobs in the first place. This is the missing other half; callers
    (reasoning.py, commerce_assistant.py) now request logprobs=True on the local
    Ollama call and pass compute_confidence(resp) through.

    Returns None — never a guessed value — when the response carries no logprobs
    (cloud providers may omit them even when requested; older Ollama builds don't
    support the param at all and litellm.drop_params silently drops it), so callers
    can tell "no signal" apart from "signal says low confidence."
    """
    try:
        choice = resp.choices[0]
        lp = getattr(choice, "logprobs", None)
        if not lp:
            return None
        content = lp.get("content") if isinstance(lp, dict) else getattr(lp, "content", None)
        if not content:
            return None
        token_logprobs = [
            (tok.get("logprob") if isinstance(tok, dict) else getattr(tok, "logprob", None))
            for tok in content
        ]
        token_logprobs = [v for v in token_logprobs if isinstance(v, (int, float))]
        if not token_logprobs:
            return None
        avg_logprob = sum(token_logprobs) / len(token_logprobs)
        return math.exp(avg_logprob)
    except Exception as exc:
        logger.debug("compute_confidence: couldn't extract logprobs: %s", exc)
        return None


def looks_unreliable(text: str, confidence: Optional[float] = None,
                     min_chars: int = 3, confidence_threshold: Optional[float] = None) -> bool:
    """Requirement (b): post-response reliability check, ported from LocalCoder's
    ollama_backend.looks_unreliable. Empty/too-short or an outright refusal always escalates; low
    logprob-confidence escalates only when both a threshold and a confidence value are available.
    Not a quality judge — a short correct answer is fine."""
    t = (text or "").strip()
    if len(t) < min_chars:
        return True
    lowered = t.lower()
    if any(m in lowered for m in ("i cannot", "i can't help", "as an ai language model")):
        return True
    if confidence_threshold is not None and confidence is not None:
        return confidence < confidence_threshold
    return False


# Single source of truth for the reply a caller substitutes when looks_degenerate() fires —
# same wording everywhere a skill's agent loop hits this, so it reads as one platform, not nine.
DEGENERATE_OUTPUT_FALLBACK = "Sorry, something went wrong generating that reply — could you try again?"


def substitute_if_degenerate(answer: str, *, skill: str, tenant_id: Optional[str] = None) -> str:
    """The shared guard every tool-calling skill applies after generating its final answer: if
    looks_degenerate() fires, log the raw (truncated) garbled text and telemetry BEFORE
    swapping in DEGENERATE_OUTPUT_FALLBACK. 2026-08-27: previously none of the 9 call sites
    logged anything when this fired, so this whole class of incident (confirmed live,
    2026-08-22: ~1000 literal '!' characters) was undiagnosable from Railway logs after the
    fact — centralizing here fixes that for every skill at once, not just the one investigated."""
    if not looks_degenerate(answer):
        return answer
    snippet = (answer or "")[:200]
    logger.warning("degenerate output caught, skill=%s len=%d snippet=%r", skill, len(answer or ""), snippet)
    try:
        from core.reasoning_telemetry import emit
        emit(system="vula-degenerate-output", task=skill, outcome="substituted",
             escalated=False, tenant_id=tenant_id, extra={"snippet": snippet, "length": len(answer or "")})
    except Exception:
        pass
    return DEGENERATE_OUTPUT_FALLBACK


def looks_degenerate(text: str, min_len: int = 20, run_threshold: int = 12,
                     min_unique_chars: int = 12, diversity_min_len: int = 200,
                     min_unique_chars_short: int = 4) -> bool:
    """Cheap, deterministic check for garbled/repeated-character LLM output — no extra LLM call,
    since the whole point is catching a case where the LLM itself already misbehaved. Anchored
    to a real production reply (off-the-hook, 2026-08-22): a WhatsApp reply that was ~1000
    literal '!' characters and nothing else, sent straight to the owner with nothing catching it.

    Two independent signals, either one enough: a long unbroken run of one character, or too few
    DISTINCT characters overall (catches a slower-building repetition loop that isn't one single
    run, e.g. alternating "!! !! !!...").

    2026-09-01: the diversity signal was a RATIO (unique/length < 0.06), which scales the wrong
    way — the longer a perfectly normal reply got, the more certainly it was flagged, because
    plain text has a roughly fixed alphabet (~27-60 distinct characters) no matter how long it
    is. At 648 characters ordinary English already tripped it; a 1421-character Gerflor price
    list — a real, correct answer of exactly the kind being asked for — was thrown away and
    replaced with "Sorry, something went wrong generating that reply". That also explains why no
    stored reply anywhere exceeded ~1200 characters: long answers were being suppressed
    wholesale.

    An absolute floor is the right shape: garbled output has 1-3 distinct characters, while any
    real sentence has well over 12, and that stays true at any length.

    Two floors, because short legitimate content can genuinely be low-diversity:
    "R100.00, R100.00, R100.00" is 6 distinct characters and perfectly valid (a real price
    repeated across line items), while "!! !! !! !!" is 2 and is garbage. So short text needs
    only `min_unique_chars_short` distinct characters to pass, and the stricter floor applies
    once the text is long enough for a real answer to have a full alphabet."""
    t = (text or "").strip()
    if len(t) < min_len:
        return False
    if re.search(r"(\S)\1{" + str(run_threshold - 1) + r",}", t):
        return True
    floor = min_unique_chars if len(t) >= diversity_min_len else min_unique_chars_short
    return len(set(t)) < floor


def _task_label(task_type: Optional[str], messages: Optional[list]) -> str:
    """A POPIA-safe task descriptor for the telemetry log: the task_type if given, else a short
    hash of the last user message. Raw prompt content is NEVER written to the log."""
    if task_type:
        return task_type
    if messages:
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("content"):
                return "hash:" + hashlib.sha256(str(m["content"]).encode("utf-8")).hexdigest()[:12]
    return "unspecified"


def _log_decision(*, run_id: str, task: str, outcome: str, escalated: bool,
                  backend: str, reason: str) -> None:
    """Append the shared telemetry envelope for one routing decision (schema/system/run_id/task/
    timestamp/outcome/escalated, + backend/reason). Answers "why did tenant data leave South
    Africa" per request. POPIA: `task` is a type label or hash, never raw prompt content.

    Sink: one JSON line to the logger (stdout, Railway-captured); also appended to $VULA_ROUTER_LOG
    when set, so verified-reasoning/tools/report.py can read it beside LocalCoder + VRL logs."""
    entry = {
        "schema": 1,
        "system": "vula-llm-router",
        "run_id": run_id,
        "task": task,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome,      # "local" | "cloud"
        "escalated": escalated,
        "backend": backend,      # ollama/<model> | openrouter/<model>
        "reason": reason,
    }
    line = json.dumps(entry)
    logger.info("route-decision %s", line)
    path = os.environ.get("VULA_ROUTER_LOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    # Persistent sink so live prod decisions accumulate for the verified-reasoning report.
    try:
        from core.reasoning_telemetry import emit
        tenant = None
        try:
            from vula.integrations.metering import _current_tenant
            tenant = _current_tenant.get()
        except Exception:
            pass
        emit(system="vula-llm-router", run_id=run_id, task=task, outcome=outcome,
             escalated=escalated, reason=reason, tenant_id=tenant, extra={"backend": backend})
    except Exception:
        pass


async def resolve_generation_route(
    model: Optional[str] = None,
    *,
    task_type: Optional[str] = None,
    messages: Optional[list] = None,
    run_id: Optional[str] = None,
) -> Tuple[str, Optional[str], str]:
    """Local-first generation route with logged, reason-bearing cloud fallback.

    Cloud is used only for a sanctioned, logged reason — (a) local unreachable, (c) genuine task
    complexity — plus the explicit operator override (prefer_cloud_llm), which is no longer the
    default and is itself logged. Requirement (b), an unreliable *local response*, is handled by
    the caller after generation via looks_unreliable() + escalate_to_cloud().

    Returns (litellm_model, api_key, api_base).
    """
    # Local model name (Ollama) and cloud fallback model name (OpenRouter) may differ — e.g.
    # "deepseek-r1:8b" locally vs "meta-llama/llama-3.3-70b" on OpenRouter. A caller-supplied
    # `model` overrides the local name only.
    local_model = model or settings.model_worker
    cloud_model = settings.model_worker_cloud or settings.model_worker
    run_id = run_id or str(uuid.uuid4())
    task = _task_label(task_type, messages)
    install_ollama_auth()  # ensure Ollama calls carry the CF-Access token before any generation

    # Dev/test mode — force the cheapest model so QA doesn't burn premium tokens.
    if os.environ.get("VULA_DEV_MODE", "").lower() in ("1", "true", "yes") and settings.openrouter_api_key:
        _log_decision(run_id=run_id, task=task, outcome="cloud", escalated=True,
                      backend=f"openrouter/{settings.model_worker_cheap}", reason="dev_mode")
        return f"openrouter/{settings.model_worker_cheap}", settings.openrouter_api_key, OPENROUTER_BASE

    # Explicit operator override — accuracy-first, but no longer the default and always logged so
    # even this path is auditable for "why did data leave SA".
    if settings.prefer_cloud_llm and settings.openrouter_api_key:
        _log_decision(run_id=run_id, task=task, outcome="cloud", escalated=True,
                      backend=f"openrouter/{cloud_model}", reason="operator_prefer_cloud")
        return f"openrouter/{cloud_model}", settings.openrouter_api_key, OPENROUTER_BASE

    # ── Local-first ──────────────────────────────────────────────────────────
    if await ollama_available(model=local_model):
        # (c) complexity — only a genuine frontier task is allowed to leave local.
        complex_reason = assess_complexity(messages=messages, task_type=task_type)
        if complex_reason and settings.openrouter_api_key:
            logger.info("Cloud for complexity (%s) — run=%s task=%s", complex_reason, run_id, task)
            _log_decision(run_id=run_id, task=task, outcome="cloud", escalated=True,
                          backend=f"openrouter/{cloud_model}", reason=complex_reason)
            return f"openrouter/{cloud_model}", settings.openrouter_api_key, OPENROUTER_BASE
        _log_decision(run_id=run_id, task=task, outcome="local", escalated=False,
                      backend=f"ollama/{local_model}", reason="local_first")
        return f"ollama/{local_model}", None, settings.ollama_base

    # (a) local unreachable → cloud, logged with the reason a regulator would ask about.
    if settings.openrouter_api_key:
        logger.info("Cloud fallback: local unreachable — run=%s task=%s", run_id, task)
        _log_decision(run_id=run_id, task=task, outcome="cloud", escalated=True,
                      backend=f"openrouter/{cloud_model}", reason="local_unreachable")
        return f"openrouter/{cloud_model}", settings.openrouter_api_key, OPENROUTER_BASE

    logger.warning("Local unreachable and no OPENROUTER_API_KEY set — generation will likely fail")
    _log_decision(run_id=run_id, task=task, outcome="local", escalated=False,
                  backend=f"ollama/{local_model}", reason="local_unreachable_no_cloud_key")
    return f"ollama/{local_model}", None, settings.ollama_base


def escalate_to_cloud(reason: str, *, run_id: Optional[str] = None,
                      task_type: Optional[str] = None) -> Optional[Tuple[str, Optional[str], str]]:
    """Return the cloud route after a local response failed the reliability check (requirement b),
    logging the escalation in the shared envelope. Returns None if no cloud key is configured (the
    caller then keeps the local answer)."""
    if not settings.openrouter_api_key:
        return None
    cloud_model = settings.model_worker_cloud or settings.model_worker
    _log_decision(run_id=run_id or str(uuid.uuid4()), task=task_type or "unspecified",
                  outcome="cloud", escalated=True, backend=f"openrouter/{cloud_model}", reason=reason)
    return f"openrouter/{cloud_model}", settings.openrouter_api_key, OPENROUTER_BASE


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
