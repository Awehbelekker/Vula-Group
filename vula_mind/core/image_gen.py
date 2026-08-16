"""
core/image_gen.py — AI product-photo generation (OpenRouter, image-output models).

Net-new capability: everything else in Vula READS images (vision); this GENERATES
them. Used by the tenant "AI photo" feature and the catalog backfill script.

Realism contract: outputs must read as real DSLR product photography — the
callers pass a style description extracted from the tenant's own reference photo
so every generated shot shares the same "studio setup".

OpenRouter image generation rides the chat-completions API with
``modalities: ["image", "text"]``; the generated image comes back base64-encoded
in ``choices[0].message.images[0].image_url.url`` (a data URL).
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Shared photorealism guardrails appended to every prompt.
REALISM_SUFFIX = (
    "True-to-life textures and colours, natural shadows, square 1:1 crop with the "
    "subject centred and fully in frame. Absolutely photorealistic — no text, no "
    "watermark, no illustration, no CGI or painterly look."
)


class ImageGenError(RuntimeError):
    """Raised when the model returns no usable image."""


async def generate_image(
    prompt: str,
    reference_jpeg: Optional[bytes] = None,
    timeout: float = 120.0,
) -> bytes:
    """Generate one image; returns raw image bytes (usually PNG/JPEG).

    ``reference_jpeg`` — optional style-anchor photo passed as an image input so
    the model matches its background/lighting/angle.
    """
    if not settings.openrouter_api_key:
        raise ImageGenError("OPENROUTER_API_KEY not set — image generation unavailable")

    content: list = [{"type": "text", "text": prompt}]
    if reference_jpeg:
        b64 = base64.b64encode(reference_jpeg).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.model_image,
                "messages": [{"role": "user", "content": content}],
                "modalities": ["image", "text"],
            },
        )
    if not resp.is_success:
        raise ImageGenError(f"OpenRouter {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    try:
        images = data["choices"][0]["message"].get("images") or []
        url = images[0]["image_url"]["url"]
    except (KeyError, IndexError, TypeError):
        text = str(data)[:300]
        raise ImageGenError(f"No image in model response: {text}")

    m = re.match(r"data:image/[a-zA-Z+]+;base64,(.+)", url)
    if not m:
        raise ImageGenError(f"Unexpected image url shape: {url[:80]}")
    return base64.b64decode(m.group(1))


def build_product_prompt(
    subject: str,
    style_description: Optional[str] = None,
    has_reference_image: bool = False,
) -> str:
    """Compose the photorealistic product-photo prompt used everywhere."""
    parts = ["Photorealistic professional food product photograph, shot on a DSLR."]
    if has_reference_image:
        parts.append(
            "Match the attached reference photo exactly: same surface, background, "
            "lighting direction, camera angle and overall composition — only the "
            "subject changes."
        )
    if style_description:
        parts.append(f"Studio setup: {style_description}")
    parts.append(f"Subject: {subject}.")
    parts.append(REALISM_SUFFIX)
    return " ".join(parts)


async def describe_reference_style(reference_jpeg: bytes) -> str:
    """One-off: use the (cheap) vision model to describe the reference photo's
    studio setup so prompt-only generations still match the house style."""
    import litellm
    from core.llm_router import resolve_cloud_vision_route

    route = resolve_cloud_vision_route()
    if not route:
        return ""
    model, api_key, api_base = route
    litellm.drop_params = True
    b64 = base64.b64encode(reference_jpeg).decode()
    resp = await litellm.acompletion(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": (
                    "Describe this product photo's studio setup in one dense sentence "
                    "for a photographer to replicate: surface/backdrop material and "
                    "colour, lighting direction and quality, camera angle, framing, "
                    "any props. Do NOT describe the product itself."
                )},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        temperature=0.1, max_tokens=150, api_key=api_key, api_base=api_base,
    )
    return (resp.choices[0].message.content or "").strip()


async def qa_check_image(image_bytes: bytes, subject: str) -> bool:
    """Cheap vision QA: is this a photorealistic photo of the subject?
    Returns True to accept. Fails open on errors (caller may still flag)."""
    import litellm
    from core.llm_router import resolve_cloud_vision_route

    route = resolve_cloud_vision_route()
    if not route:
        return True
    model, api_key, api_base = route
    litellm.drop_params = True
    b64 = base64.b64encode(image_bytes).decode()
    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        f"Answer YES or NO only. Is this image a photorealistic "
                        f"photograph (not an illustration/CGI) that plausibly shows: "
                        f"{subject}? It must contain no visible text or watermark."
                    )},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
            temperature=0.0, max_tokens=5, api_key=api_key, api_base=api_base,
        )
        answer = (resp.choices[0].message.content or "").strip().upper()
        return answer.startswith("YES")
    except Exception as exc:  # QA must never block harder than the gen itself
        logger.warning("Image QA check failed (accepting image): %s", exc)
        return True
