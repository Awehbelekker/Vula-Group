"""Local-LLM watch — tell Ian when the SA inference box stops answering (2026-10-19).

The local Ollama host is reached through a Cloudflare tunnel (settings.ollama_base). When it goes
away the LLM router quietly falls back to the cloud for every call (reason "local_unreachable"),
so nothing looks broken, and nobody noticed for 3½ days (15–19 Oct: Cloudflare 530, every call
cloud). This probes it every 5 minutes on the scheduler leader and WhatsApps
settings.platform_support_phone:

  * once it has been down for local_llm_alert_after_min minutes,
  * again every local_llm_alert_repeat_h hours while it stays down,
  * once when it answers again (only if an alert went out).

Each alert is also written to vula_reasoning_telemetry (system 'local-llm-watch'), which is where
the state comes back from after a restart, so a redeploy mid-outage doesn't re-alert. Only
labels, minutes and timestamps go there or into the message — no tenant content.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from config import settings

log = logging.getLogger(__name__)

CHECK_EVERY_S = 300
_PROBE_TIMEOUT_S = 8.0
_SYSTEM = "local-llm-watch"
_SAST = ZoneInfo("Africa/Johannesburg")

_state: Dict[str, Any] = {"down_since": None, "alerted_at": None, "restored": False}


def _reset() -> None:
    _state.update(down_since=None, alerted_at=None, restored=False)


async def probe() -> Tuple[bool, str]:
    """(up, why-not label). A plain GET /api/tags with the Cloudflare Access headers, with a
    longer timeout than the router's per-request probe so a slow minute isn't an outage."""
    import httpx
    from core.llm_router import _ollama_headers
    base = (settings.ollama_base or "").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            r = await client.get(f"{base}/api/tags", headers=_ollama_headers() or None)
        return (True, "") if r.status_code == 200 else (False, f"HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


def _hint(why: str) -> str:
    if why == "HTTP 530" or why == "HTTP 1033":
        return "Cloudflare can't reach the machine: check it is on and awake, and that cloudflared and Ollama are running"
    if why in ("HTTP 401", "HTTP 403"):
        return "Cloudflare Access is refusing the service token: check OLLAMA_CF_ACCESS_CLIENT_ID/SECRET"
    if why in ("ReadTimeout", "ConnectTimeout", "TimeoutException"):
        return "the machine isn't answering in time: it may be overloaded or asleep"
    return "check the machine, cloudflared and Ollama"


def _dur(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}h {m:02d}m"
    return f"{h // 24} days {h % 24}h"


def _at(ts: float) -> str:
    return datetime.fromtimestamp(ts, _SAST).strftime("%a %d %b %H:%M SAST")


def down_message(down_since: float, now: float, why: str) -> str:
    """One line — it goes into a WhatsApp template parameter, which can't hold newlines."""
    return (f"Vula local AI server unreachable for {_dur(now - down_since)} (since {_at(down_since)}; "
            f"{why}). All AI work is running on the cloud model meanwhile. Fix: {_hint(why)}.")


def up_message(down_since: float, now: float) -> str:
    return (f"Vula local AI server is back after {_dur(now - down_since)} "
            f"(down since {_at(down_since)}). AI work is running locally again.")


async def _send(text: str) -> bool:
    from vula.integrations.platform_support import alert_operator
    return await alert_operator(text)


def _record(outcome: str, why: str, down_since: float, now: float, sent: bool) -> None:
    from core.reasoning_telemetry import emit
    emit(system=_SYSTEM, task="ollama_tunnel", outcome=outcome, reason=why or None,
         extra={"down_since": down_since, "minutes": int((now - down_since) // 60), "sent": sent})


def _restore() -> None:
    """After a restart: if the last alert row is an unresolved 'down'/'reminder', carry on from it."""
    _state["restored"] = True
    try:
        from vula.commerce import service
        rows = (service._client().table("vula_reasoning_telemetry").select("outcome,extra,created_at")
                .eq("system", _SYSTEM).order("created_at", desc=True).limit(1).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        log.debug("local-llm watch state not restored: %s", exc)
        return
    if rows and rows[0].get("outcome") in ("down", "reminder"):
        extra = rows[0].get("extra") or {}
        try:
            _state["down_since"] = float(extra["down_since"])
            _state["alerted_at"] = datetime.fromisoformat(
                str(rows[0]["created_at"]).replace("Z", "+00:00")).timestamp()
        except Exception:  # noqa: BLE001
            pass


async def tick(now: Optional[float] = None) -> Optional[str]:
    """One check. Returns 'down', 'reminder' or 'up' when it alerted, else None."""
    now = time.time() if now is None else now
    if not _state["restored"]:
        await asyncio.to_thread(_restore)
    up, why = await probe()
    if up:
        down_since, alerted = _state["down_since"], _state["alerted_at"]
        _state.update(down_since=None, alerted_at=None)
        if alerted and down_since:
            sent = await _send(up_message(down_since, now))
            log.warning("Local LLM back after %s (alert sent=%s)", _dur(now - down_since), sent)
            _record("up", "", down_since, now, sent)
            return "up"
        return None

    if _state["down_since"] is None:
        _state["down_since"] = now
        log.warning("Local LLM unreachable (%s) — watching", why)
    down_since = _state["down_since"]
    if now - down_since < settings.local_llm_alert_after_min * 60:
        return None
    alerted = _state["alerted_at"]
    if alerted is not None and now - alerted < settings.local_llm_alert_repeat_h * 3600:
        return None
    kind = "down" if alerted is None else "reminder"
    sent = await _send(down_message(down_since, now, why))
    _state["alerted_at"] = now
    log.warning("Local LLM unreachable for %s (%s) — alert sent=%s", _dur(now - down_since), why, sent)
    _record(kind, why, down_since, now, sent)
    return kind


def enabled() -> bool:
    """Only watch a remote host: a bare localhost Ollama (dev, tests) has nobody to alert."""
    base = (settings.ollama_base or "").lower()
    return bool(base) and "localhost" not in base and "127.0.0.1" not in base


async def run_forever() -> None:
    if not enabled():
        return
    await asyncio.sleep(120)  # settle on boot
    while True:
        try:
            await tick()
        except Exception as exc:  # noqa: BLE001
            log.warning("local-llm watch tick failed: %s", exc)
        await asyncio.sleep(CHECK_EVERY_S)
