"""
scripts/ingest_tenant_kb.py — Ingest a tenant's knowledge base into Qdrant.

Builds the per-tenant Qdrant collection (vula_<tenant_id>) that the
commerce_assistant and reasoning skills query for grounded answers. Point it
at a file or a directory of business documents (delivery policy, FAQs, catalog
notes, returns policy, etc.), or run with no path to seed a small built-in KB
for known tenants like Off the Hook.

Usage:
    cd vula_mind
    python scripts/ingest_tenant_kb.py off-the-hook ./kb/off_the_hook
    python scripts/ingest_tenant_kb.py off-the-hook ./kb/delivery_policy.pdf
    python scripts/ingest_tenant_kb.py off-the-hook        # seed built-in KB

Requires Ollama (for bge-m3 embeddings) or OPENROUTER_API_KEY, and a running
Qdrant (local or Qdrant Cloud via QDRANT_API_KEY).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vula.ingestion.pipeline import VulaIngestionPipeline  # noqa: E402

# ── Built-in seed knowledge bases (used when no path is supplied) ──────────────
# Plain-text business context that grounds the assistant's answers. Extend per
# tenant as policies firm up; richer/longer docs should be ingested as files.

_BUILTIN_KB: dict[str, dict[str, str]] = {
    "off-the-hook": {
        "delivery_policy.md": (
            "# Off the Hook — Delivery\n\n"
            "We deliver fresh daily catch door-to-door across Cape Town.\n"
            "Standard delivery is R80. Free delivery on orders over R500.\n"
            "Delivery slots: morning (08:00–12:00), afternoon (12:00–17:00), "
            "and express (2-hour window, +R50).\n"
            "Orders placed before 10:00 can be delivered the same day, subject to stock.\n"
        ),
        "about.md": (
            "# About Off the Hook\n\n"
            "Off the Hook supplies Cape Town's freshest daily catch: linefish "
            "(yellowtail, snoek, kob, red roman), shellfish and prawns, West Coast "
            "crayfish, box deals, and smoked fish.\n"
            "Fresh fish is sold per kg and rotates weekly with the catch. "
            "Frozen seafood is sold per pack at fixed prices.\n"
            "Contact: 073 781 5979.\n"
        ),
        "faq.md": (
            "# Off the Hook — FAQ\n\n"
            "Q: How do I pay? A: We send a secure Yoco payment link at checkout.\n"
            "Q: Can I track my order? A: Yes — reply with your order number "
            "(e.g. OTH-00042) and we'll send an update.\n"
            "Q: Is the fish fresh? A: Yes, linefish is the daily catch and subject "
            "to availability. We'll suggest alternatives if something sells out.\n"
        ),
    },
}


async def _ingest_path(tenant_id: str, path: Path) -> int:
    pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
    total = 0
    if path.is_dir():
        results = await pipeline.ingest_directory(path)
        for r in results:
            print(f"  {r.status:8} {r.filename:40} chunks={r.chunks_stored}")
            total += r.chunks_stored
    else:
        r = await pipeline.ingest_file(path)
        print(f"  {r.status:8} {r.filename:40} chunks={r.chunks_stored}")
        if r.error:
            print(f"           error: {r.error}")
        total += r.chunks_stored
    return total


async def _ingest_builtin(tenant_id: str) -> int:
    kb = _BUILTIN_KB.get(tenant_id)
    if not kb:
        print(f"No built-in KB for tenant '{tenant_id}'. Supply a file or directory path.")
        print(f"Known built-in tenants: {', '.join(_BUILTIN_KB) or '(none)'}")
        return 0
    pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
    total = 0
    for filename, content in kb.items():
        r = await pipeline.ingest_text(content, filename=filename)
        print(f"  {r.status:8} {filename:40} chunks={r.chunks_stored}")
        if r.error:
            print(f"           error: {r.error}")
        total += r.chunks_stored
    return total


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/ingest_tenant_kb.py <tenant_id> [path-to-file-or-dir]")
        sys.exit(1)

    tenant_id = sys.argv[1]
    print(f"\n🌿 Vula KB ingestion — tenant: {tenant_id}")
    print(f"   Collection: vula_{tenant_id.replace('-', '_')}\n")

    if len(sys.argv) >= 3:
        path = Path(sys.argv[2])
        if not path.exists():
            print(f"Path not found: {path}")
            sys.exit(1)
        total = await _ingest_path(tenant_id, path)
    else:
        total = await _ingest_builtin(tenant_id)

    print(f"\n✓ Done — {total} chunks stored for tenant '{tenant_id}'.")


if __name__ == "__main__":
    asyncio.run(main())
