"""
core/transcribe.py — WhatsApp voice-note → text.

SA customers send voice orders constantly ("gee my twee hake, dankie"), so a
customer that sends audio must get the same service as one who types. This turns
a downloaded audio file into text via an OpenAI-compatible /audio/transcriptions
endpoint (whisper), then the caller feeds the text into the normal commerce /
knowledge assistant.

Local-first, same philosophy as the LLM router:
  1. TRANSCRIBE_BASE (+ optional key) — point at a faster-whisper server on the SA
     GPU when it's up, or a cheap cloud provider (Groq whisper-large-v3).
  2. OPENAI_API_KEY — fall back to api.openai.com (whisper-1).
  3. Neither configured / call fails → return None, and the caller asks the
     customer to type instead. We never fabricate a transcript.

Whisper auto-detects language, so this doubles as the language detector for the
multi-language reply path — an Afrikaans voice note comes back as Afrikaans text
and the assistant mirrors it.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

from config import settings
from core import reasoning_telemetry as _telemetry

logger = logging.getLogger(__name__)


def _cf_access_headers() -> dict:
    """Cloudflare Access service-token headers for a LOCAL Whisper tunnel (e.g. whisper.vula-ai.com).

    Same pattern as the Ollama tunnel: the SA GPU sits behind Cloudflare Access, so Railway must
    present a service token or it's blocked at the edge (keeps the inference node private, POPIA).
    Falls back to the Ollama service token since it's the same Cloudflare account — add the Whisper
    hostname to that Access application (or a policy allowing the same token). Empty when unset, so
    a bare-localhost or cloud provider needs no token."""
    cid = os.environ.get("TRANSCRIBE_CF_ACCESS_CLIENT_ID") or os.environ.get("OLLAMA_CF_ACCESS_CLIENT_ID", "")
    csec = os.environ.get("TRANSCRIBE_CF_ACCESS_CLIENT_SECRET") or os.environ.get("OLLAMA_CF_ACCESS_CLIENT_SECRET", "")
    return {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": csec} if cid and csec else {}


def _provider() -> Optional[tuple[str, str, str]]:
    """Return (base_url, api_key, model) for the first configured provider, else None."""
    if settings.transcribe_base:
        base = settings.transcribe_base.rstrip("/")
        return base, settings.transcribe_api_key, settings.transcribe_model
    if settings.openai_api_key:
        return "https://api.openai.com/v1", settings.openai_api_key, "whisper-1"
    return None


async def transcribe_audio(
    audio: bytes,
    *,
    mime_type: str = "audio/ogg",
    filename: str = "voice.ogg",
    tenant_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Transcribe audio bytes → (text, language_code). Both None if no provider is configured
    or the call fails — the caller must handle that gracefully (never guess). The language code
    is Whisper's own detection (e.g. 'en', 'af', 'zu') and is a reliable per-customer signal."""
    prov = _provider()
    if not prov or not audio:
        if not prov:
            logger.info("voice note received but no transcription provider configured")
        return None, None
    base, key, model = prov
    t0 = time.monotonic()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    # Local-first: if the endpoint is the SA GPU behind Cloudflare Access, attach the service token.
    headers.update(_cf_access_headers())
    files = {"file": (filename, audio, mime_type)}
    data = {"model": model, "response_format": "json"}
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{base}/audio/transcriptions", headers=headers, files=files, data=data
            )
            resp.raise_for_status()
            payload = resp.json()
            text = (payload.get("text") or "").strip()
            language = (payload.get("language") or None)
        _telemetry.emit(
            system="vula-transcribe", task="voice_note", outcome="ok",
            tenant_id=tenant_id, reason=base,
            extra={"ms": int((time.monotonic() - t0) * 1000), "bytes": len(audio),
                   "model": model, "language": language},
        )
        logger.info("transcribed voice note (%d bytes, lang=%s) in %dms via %s",
                    len(audio), language, int((time.monotonic() - t0) * 1000), base)
        return (text or None), language
    except Exception as exc:
        logger.warning("voice-note transcription failed via %s: %s", base, exc)
        _telemetry.emit(
            system="vula-transcribe", task="voice_note", outcome="error",
            tenant_id=tenant_id, reason=str(exc)[:120],
        )
        return None, None
