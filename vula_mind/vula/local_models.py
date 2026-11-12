"""Local models — make sure the SA box has the models Vula is set to use (12 Nov 2026).

Ian: "can you do all the pulling". The box is reached only through the Cloudflare tunnel, which
this server already uses for every local call, and Ollama's own API downloads a model when asked
(POST /api/pull). settings.local_models_ensure lists the models the box should hold
("qwen3:8b,glm-ocr"); on the scheduler leader, after boot, each one missing from /api/tags is
pulled, one at a time, and the outcome is written to vula_reasoning_telemetry (system
'local-models': model name, outcome, seconds, Ollama's error text) — never any tenant content.
Nothing is ever deleted from the box. When everything is present the loop stops until the next boot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from config import settings

log = logging.getLogger(__name__)

SYSTEM = "local-models"
MODEL_RE = re.compile(r"^[a-z0-9][\w.\-]*(/[\w.\-]+)?(:[\w.\-]+)?$", re.I)
RETRY_EVERY_S = 600          # box down, or a pull failed: try again in 10 minutes
_running = False


def wanted() -> List[str]:
    """settings.local_models_ensure as a clean list of valid Ollama names (others are logged)."""
    out = []
    for m in (settings.local_models_ensure or "").split(","):
        m = m.strip()
        if not m:
            continue
        if MODEL_RE.match(m):
            out.append(m)
        else:
            log.warning("LOCAL_MODELS_ENSURE: %r is not an Ollama model name — ignored", m)
    return list(dict.fromkeys(out))


def _full(name: str) -> str:
    return name if ":" in name.split("/")[-1] else f"{name}:latest"


def _base() -> str:
    return (settings.ollama_base or "").rstrip("/")


def _headers() -> Optional[dict]:
    from core.llm_router import _ollama_headers
    return _ollama_headers() or None


async def installed(client: httpx.AsyncClient) -> set:
    r = await client.get(f"{_base()}/api/tags", headers=_headers(), timeout=15)
    r.raise_for_status()
    return {_full(m.get("name") or "") for m in (r.json().get("models") or [])}


async def pull(client: httpx.AsyncClient, model: str) -> Dict[str, Any]:
    """Ask Ollama to download `model`, reading its progress stream. {"ok", "error", "seconds"}."""
    t0 = time.monotonic()
    last_pct, error, done = -10, None, False
    try:
        async with client.stream("POST", f"{_base()}/api/pull", headers=_headers(),
                                 json={"model": model, "stream": True},
                                 timeout=httpx.Timeout(30.0, read=900.0)) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("error"):
                    error = str(ev["error"])[:300]
                    break
                total, got = ev.get("total") or 0, ev.get("completed") or 0
                if total:
                    pct = int(100 * got / total)
                    if pct >= last_pct + 10:
                        last_pct = pct
                        log.info("local model %s: %s %d%%", model, ev.get("status") or "", pct)
                if ev.get("status") == "success":
                    done = True
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"[:300]
    if not done and not error:
        error = "the download stopped before Ollama reported success"
    return {"ok": done and not error, "error": error, "seconds": int(time.monotonic() - t0)}


def _record(model: str, outcome: str, error: Optional[str] = None, seconds: Optional[int] = None) -> None:
    try:
        from core.reasoning_telemetry import emit
        emit(system=SYSTEM, task="pull", outcome=outcome, reason=error,
             extra={"model": model, "seconds": seconds})
    except Exception:  # noqa: BLE001
        pass


async def ensure_once(client: Optional[httpx.AsyncClient] = None) -> Dict[str, str]:
    """One pass: {model: "present" | "pulled" | "failed" | "unreachable"}."""
    models = wanted()
    if not models:
        return {}
    own = client is None
    client = client or httpx.AsyncClient()
    out: Dict[str, str] = {}
    try:
        try:
            have = await installed(client)
        except Exception as exc:  # noqa: BLE001
            log.info("local models: box not reachable (%s) — will try again", type(exc).__name__)
            return {m: "unreachable" for m in models}
        for m in models:
            if _full(m) in have:
                out[m] = "present"
                continue
            log.warning("local model %s is not on the box — asking Ollama to download it", m)
            res = await pull(client, m)
            out[m] = "pulled" if res["ok"] else "failed"
            _record(m, out[m], res["error"], res["seconds"])
            if res["ok"]:
                log.warning("local model %s downloaded in %ss", m, res["seconds"])
            else:
                log.warning("local model %s could not be downloaded: %s", m, res["error"])
    finally:
        if own:
            await client.aclose()
    return out


async def run_forever(first_delay_s: float = 90.0) -> None:
    """After boot: pass until every wanted model is on the box, then stop (one runner per process)."""
    global _running
    if _running or not wanted():
        return
    _running = True
    try:
        await asyncio.sleep(first_delay_s)
        while True:
            res = await ensure_once()
            if res and all(v in ("present", "pulled") for v in res.values()):
                log.info("local models all present: %s", ", ".join(res))
                return
            await asyncio.sleep(RETRY_EVERY_S)
    finally:
        _running = False
