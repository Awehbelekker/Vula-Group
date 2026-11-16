"""AI health check — make a dead model or an empty web search loud (16 Nov 2026).

Ian: "we need to be accurate". Two outages ran silently for days:
  * Gemini 2.5 Flash answered no call from 13 Nov (no endpoint under zero data retention). The
    fallback chain made most calls "work" on another model, cloud scans had none and failed, and
    nobody was told — the operator WhatsApp needs Vula's own number, which isn't set.
  * DuckDuckGo gave Railway's servers a page with no results, so research found nothing.

Every CHECK_EVERY_S on the scheduler leader this:
  * sends each cloud model Vula is set to use a 5-token "Reply OK" (the vision model also gets a
    1×1 image), under zero retention like every call, with the fallback chain OFF so a dead model
    can't hide behind a live one;
  * runs one web search (no tenant words) and reads how many real searches found nothing;
  * records one vula_health_events row per check (tenant 'platform', kind 'ai_check');
  * alerts when a check goes from ok to failing, and when it recovers: operator WhatsApp and the
    team email where they're set up, and always Master › Health (`status()`).
Only model names, labels and counts are recorded or sent — never tenant content.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from config import settings

log = logging.getLogger(__name__)

CHECK_EVERY_S = 6 * 3600
PLATFORM = "platform"
KIND = "ai_check"
_PROBE_TIMEOUT_S = 45.0
SEARCH_QUERY = "cement 50kg price South Africa"
# a 1×1 white PNG — enough for a vision model to have to accept an image
_PIXEL = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==")
# real searches since the last run: failing when at least this many and nearly all empty
_SEARCH_MIN, _SEARCH_EMPTY_SHARE = 5, 0.8

_state: Dict[str, Dict[str, Any]] = {}   # check name -> {"ok", "why", "since"}
_restored = False


def _bare(m: str) -> str:
    return (m or "").strip().removeprefix("openrouter/")


def cloud_models() -> Dict[str, List[str]]:
    """{model: [what it's used for]} — every OpenRouter model Vula is set to call."""
    from core import llm_router
    uses: Dict[str, List[str]] = {}

    def add(m: str, use: str) -> None:
        m = _bare(m)
        if m and "/" in m:
            uses.setdefault(m, [])
            if use not in uses[m]:
                uses[m].append(use)

    add(settings.model_vision, "scanning")
    add(settings.model_worker_cheap, "document analysis")
    add(settings.model_worker_cloud, "general answers")
    for task, m in llm_router._TASK_CLOUD_DEFAULTS.items():
        add(m, task.replace("_", " "))
    for m in (settings.cloud_model_fallbacks or "").split(","):
        add(m, "fallback")
    try:
        mapping = json.loads(settings.cloud_model_by_task or "{}")
        for task, m in (mapping.items() if isinstance(mapping, dict) else []):
            add(str(m), str(task).replace("_", " "))
    except Exception:  # noqa: BLE001
        pass
    return uses


def _why(exc: BaseException) -> str:
    from core.llm_router import _context_too_long, _is_not_found
    if _is_not_found(exc):
        return "no endpoint (not served under zero retention?)"
    if _context_too_long(exc):
        return "prompt too long for its provider"
    if isinstance(exc, asyncio.TimeoutError):
        return "timed out"
    return type(exc).__name__


async def probe_model(model: str, vision: bool = False) -> Tuple[bool, str]:
    """(answered, why-not). The named model only — no fallback."""
    import litellm
    from core.llm_router import OPENROUTER_BASE, no_cloud_fallback
    content: Any = "Reply with the word OK."
    if vision:
        content = [{"type": "text", "text": "Reply with the word OK."},
                   {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_PIXEL}"}}]
    try:
        with no_cloud_fallback():
            resp = await asyncio.wait_for(litellm.acompletion(
                model=f"openrouter/{_bare(model)}", messages=[{"role": "user", "content": content}],
                max_tokens=5, temperature=0, api_key=settings.openrouter_api_key, api_base=OPENROUTER_BASE),
                timeout=_PROBE_TIMEOUT_S)
        if not resp.choices:
            return False, "empty reply"
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, _why(exc)


async def probe_search() -> Tuple[bool, str]:
    """One search with no tenant words, plus what real searches found since the last run."""
    from core.skills import web_search
    seen = web_search.take_stats()
    try:
        hits = await web_search._ddg_search(SEARCH_QUERY, limit=3)
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__
    if not hits:
        return False, ("no results" + ("" if settings.serper_api_key else " (SERPER_API_KEY not set)"))
    if seen["searches"] >= _SEARCH_MIN and seen["empty"] / seen["searches"] >= _SEARCH_EMPTY_SHARE:
        return False, f"{seen['empty']} of {seen['searches']} searches found nothing"
    return True, ""


async def run_checks() -> List[Dict[str, Any]]:
    """[{name, ok, why, uses}] — one per cloud model, plus web search."""
    out: List[Dict[str, Any]] = []
    if settings.openrouter_api_key:
        models = cloud_models()
        vision = _bare(settings.model_vision)
        results = await asyncio.gather(*(probe_model(m, vision=(m == vision)) for m in models))
        for (m, uses), (ok, why) in zip(models.items(), results):
            out.append({"name": f"model:{m}", "ok": ok, "why": why, "uses": uses})
    ok, why = await probe_search()
    out.append({"name": "web_search", "ok": ok, "why": why, "uses": ["research", "build-ups", "market prices"]})
    return out


def _label(check: Dict[str, Any]) -> str:
    name = check["name"]
    if name.startswith("model:"):
        return f"Cloud model {name[6:]} ({', '.join(check.get('uses') or [])})"
    return "Web search"


def _restore() -> None:
    """After a restart: the last recorded result per check (so a redeploy mid-outage doesn't
    re-alert, and a recovery is still noticed)."""
    global _restored
    _restored = True
    try:
        from core.mass_mind.health import _client
        since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        rows = (_client().table("vula_health_events").select("detail,created_at").eq("tenant_id", PLATFORM)
                .eq("kind", KIND).gte("created_at", since).order("created_at", desc=True).limit(200)
                .execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("ai health state not restored: %s", exc)
        return
    for r in reversed(rows):   # oldest first, so the newest wins
        d = r.get("detail") or {}
        if d.get("name"):
            _state[d["name"]] = {"ok": bool(d.get("ok")), "why": d.get("why") or "",
                                 "since": d.get("since") or r.get("created_at")}


def _record(check: Dict[str, Any], since: str) -> None:
    from core.mass_mind.health import record_event
    record_event(PLATFORM, KIND, {"name": check["name"], "ok": check["ok"], "why": check["why"],
                                  "uses": check.get("uses") or [], "since": since})


async def _alert(text: str) -> Dict[str, bool]:
    sent = {"whatsapp": False, "email": False}
    try:
        from vula.integrations.platform_support import alert_operator
        sent["whatsapp"] = await alert_operator(text)
    except Exception as exc:  # noqa: BLE001
        log.debug("ai health whatsapp alert failed: %s", exc)
    try:
        from vula.api.email import send_ops_alert_email
        sent["email"] = await send_ops_alert_email("Vula AI health: " + text[:80], text)
    except Exception as exc:  # noqa: BLE001
        log.debug("ai health email alert failed: %s", exc)
    return sent


async def tick() -> List[Dict[str, Any]]:
    """One run. Returns the checks that changed state (each alerted)."""
    if not _restored:
        await asyncio.to_thread(_restore)
    now = datetime.now(timezone.utc).isoformat()
    changed: List[Dict[str, Any]] = []
    for check in await run_checks():
        prev = _state.get(check["name"])
        flipped = (prev is None and not check["ok"]) or (prev is not None and prev["ok"] != check["ok"])
        since = now if (prev is None or flipped) else prev.get("since") or now
        _state[check["name"]] = {"ok": check["ok"], "why": check["why"], "since": since}
        await asyncio.to_thread(_record, check, since)
        if flipped:
            changed.append(check)
    if changed:
        down = [c for c in changed if not c["ok"]]
        back = [c for c in changed if c["ok"]]
        parts = []
        if down:
            parts.append("FAILING: " + "; ".join(f"{_label(c)}: {c['why']}" for c in down))
        if back:
            parts.append("working again: " + "; ".join(_label(c) for c in back))
        text = "Vula AI health — " + ". ".join(parts) + "."
        sent = await _alert(text)
        log.warning("%s (whatsapp sent=%s, email sent=%s)", text, sent["whatsapp"], sent["email"])
    else:
        failing = [c for c in _state.values() if not c["ok"]]
        log.info("ai health: %d checks, %d failing", len(_state), len(failing))
    return changed


def status() -> Dict[str, Any]:
    """For Master › Health: the latest result per check from the last 2 days."""
    try:
        from core.mass_mind.health import _client
        since = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        rows = (_client().table("vula_health_events").select("detail,created_at").eq("tenant_id", PLATFORM)
                .eq("kind", KIND).gte("created_at", since).order("created_at", desc=True).limit(300)
                .execute().data or [])
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "checks": [], "failing": []}
    latest: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = r.get("detail") or {}
        if d.get("name") and d["name"] not in latest:
            latest[d["name"]] = {"name": d["name"], "label": _label(d), "ok": bool(d.get("ok")),
                                 "why": d.get("why") or "", "since": d.get("since"), "checked_at": r.get("created_at")}
    checks = sorted(latest.values(), key=lambda c: (c["ok"], c["name"]))
    alerting = []
    if not settings.platform_support_phone:
        alerting.append("PLATFORM_SUPPORT_PHONE")
    if not settings.team_email or not settings.resend_api_key:
        alerting.append("TEAM_EMAIL + RESEND_API_KEY")
    return {"checks": checks, "failing": [c for c in checks if not c["ok"]],
            "last_run": rows[0]["created_at"] if rows else None,
            "alerts_not_set_up": alerting}


async def run_forever() -> None:
    await asyncio.sleep(120)   # settle on boot
    while True:
        started = time.monotonic()
        try:
            await tick()
        except Exception as exc:  # noqa: BLE001
            log.warning("ai health tick failed: %s", exc)
        await asyncio.sleep(max(60.0, CHECK_EVERY_S - (time.monotonic() - started)))

