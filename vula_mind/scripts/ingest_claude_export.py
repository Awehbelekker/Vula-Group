"""
scripts/ingest_claude_export.py

Parses a Claude.ai conversation export and ingests only work-related
conversations into Vula tenant knowledge bases.

HOW TO GET THE EXPORT:
  claude.ai → profile (bottom left) → Settings → Privacy → Export Data
  You receive a download link by email → the folder contains:
    conversations.json  — all conversations
    projects/           — project docs (files you uploaded to Claude projects)
    users.json / memories.json

WHAT THIS DOES:
  Scores every conversation on actual human message content (not title)
  Splits into two buckets:
    digg-arch  → architecture practice conversations → tenant: digg-demo
    vula-tech  → software/AI development conversations → tenant: vula-internal
  Shows a preview and asks for confirmation before ingesting anything
  Also ingests project docs from work-related Claude projects

RUN:
  cd vula_mind
  python -m scripts.ingest_claude_export "C:\\path\\to\\export_folder"
  python -m scripts.ingest_claude_export "C:\\path\\to\\conversations.json"

OPTIONS:
  --dry-run   Preview only, do not ingest
  --yes       Skip confirmation prompt
  --arch-only Only ingest DIGG architecture conversations
  --tech-only Only ingest Vula tech conversations
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

# ─── Scoring keyword sets ─────────────────────────────────────────────────────

# Architecture/construction keywords → DIGG KB
_ARCH_KEYWORDS = {
    'architecture': 40, 'construction': 30, 'digg': 50, 'sacap': 40,
    'nhbrc': 40, 'jbcc': 35, 'architect': 25, 'renovation': 25,
    'contractor': 25, 'floor plan': 25, 'elevation': 20,
    'municipality': 30, 'sqm': 25, 'boq': 35, 'bill of quantities': 40,
    'occupation certificate': 30, 'practical completion': 30,
    'fee schedule': 35, 'building submission': 40, 'council': 20,
    'heritage': 25, 'sans 10400': 40, 'site visit': 25,
    'fitout': 30, 'porterfield': 45, 'bokaap': 45, 'parow': 40,
    'adel': 30, 'skypod': 25, 'life changers': 30, 'mezzanine': 30,
    'title block': 30, 'revit': 25, 'autocad': 25, 'drawing': 20,
    'health and safety': 25, 'tender': 25, 'project programme': 30,
    'critical path': 30, 'invoice': 20, 'wadi saffar': 35,
    'swartland': 30, 'fire installation': 30, 'cloud revit': 35,
}

# Tech/software/AI keywords → Vula tech KB
_TECH_KEYWORDS = {
    'flowcrew': 40, 'vula wave': 40, 'vula business': 40, 'vula mind': 40,
    'vula': 25, 'sporty': 35, 'skypod': 25,
    'fastapi': 30, 'supabase': 30, 'qdrant': 35, 'whatsapp': 20,
    'payfast': 30, 'react native': 30, 'expo': 20, 'nextjs': 25,
    'litellm': 35, 'ollama': 35, 'deepseek': 35, 'langchain': 25,
    'agent': 20, 'llm': 25, 'rag': 30, 'embedding': 25,
    'thinkmesh': 40, 'hrm': 25, 'soul': 20,
    'docker': 20, 'railway': 20, 'vercel': 20, 'github': 15,
    'api': 15, 'backend': 20, 'frontend': 20, 'database': 15,
    'universal soul': 40, 'soul ai': 40, 'hermes': 20,
    'booking platform': 25, 'watersports': 25, 'ikhokha': 30,
    'nfc payment': 30, 'multi-tenant': 30, 'multi agent': 30,
    'vox telecoms': 30,
}

# Negatives — clearly personal
_NEGATIVES = {
    'estate distribution': -90, 'family contingency': -70,
    'recipe': -60, 'vacation': -50, 'holiday': -50,
    'restaurant': -50, 'movie': -50, 'birthday': -60,
    'instagram': -50, 'netflix': -60, 'spotify': -50,
    'god and fatherhood': -90, 'resignation notice': -60,
    'school notice chat': -70, 'divorce': -80,
    'travel': -30, 'therapy': -70,
}


@dataclass
class ScoredConv:
    uuid: str
    title: str
    arch_score: int
    tech_score: int
    message_count: int
    bucket: str          # 'arch', 'tech', 'skip'
    formatted_text: str


def _extract_human_text(conv: dict, max_chars: int = 4000) -> str:
    """Extract only the human messages from a conversation."""
    parts = []
    for m in conv.get('chat_messages', []):
        if m.get('sender') in ('human', 'user'):
            t = m.get('text') or ''
            if isinstance(t, list):
                t = ' '.join(b.get('text', '') if isinstance(b, dict) else '' for b in t)
            parts.append(t[:600])
            if sum(len(p) for p in parts) > max_chars:
                break
    return ' '.join(parts)


def _score(text: str, keywords: dict) -> int:
    lower = text.lower()
    score = 0
    for kw, w in keywords.items():
        if kw in lower:
            score += w
    for kw, pen in _NEGATIVES.items():
        if kw in lower:
            score += pen
    return max(0, score)


def _format_conv(conv: dict) -> str:
    lines = [f"# {conv.get('name', 'Untitled')}\n"]
    if conv.get('summary'):
        lines.append(f"*{conv['summary']}*\n")
    for m in conv.get('chat_messages', []):
        role = m.get('sender', '')
        label = 'User' if role in ('human', 'user') else 'Vula'
        t = m.get('text') or ''
        if isinstance(t, list):
            t = ' '.join(b.get('text', '') if isinstance(b, dict) else '' for b in t)
        t = t.strip()
        if t:
            lines.append(f"**{label}:** {t}\n")
    return '\n'.join(lines)


def score_all(conversations: list[dict]) -> list[ScoredConv]:
    results = []
    import os
    _THRESH = int(os.environ.get("INGEST_THRESHOLD", "60"))
    # When set, ALL work-related convs (arch OR tech above threshold) go to 'arch'
    _MERGE_TO_ARCH = os.environ.get("INGEST_MERGE_TO_ARCH", "false").lower() == "true"

    for c in conversations:
        title = c.get('name', '')
        human_text = _extract_human_text(c)
        full = title + ' ' + human_text

        arch = _score(full, _ARCH_KEYWORDS)
        tech = _score(full, _TECH_KEYWORDS)

        if _MERGE_TO_ARCH:
            # Single-tenant mode: any work-relevant conversation → arch bucket
            bucket = 'arch' if max(arch, tech) >= _THRESH else 'skip'
        elif arch >= _THRESH and arch >= tech:
            bucket = 'arch'
        elif tech >= _THRESH and tech > arch:
            bucket = 'tech'
        else:
            bucket = 'skip'

        results.append(ScoredConv(
            uuid=c.get('uuid', ''),
            title=title,
            arch_score=arch,
            tech_score=tech,
            message_count=len(c.get('chat_messages', [])),
            bucket=bucket,
            formatted_text=_format_conv(c),
        ))

    results.sort(key=lambda x: max(x.arch_score, x.tech_score), reverse=True)
    return results


def extract_project_docs(projects_dir: Path) -> dict[str, list[dict]]:
    """
    Returns {'arch': [...], 'tech': [...]} lists of project doc dicts
    with keys: filename, content, project_name
    """
    _ARCH_PROJECTS = {
        'adel addition', 'hpc- bokaap', 'parow mixed use development',
        'porterfield', 'skypod', 'life changers', 'friends of echium park',
    }
    _TECH_PROJECTS = {
        'flowcrew', 'repositories', 'sporty tv', 'vox telecoms',
        'research ai mass frame work multi agents',
    }

    docs = {'arch': [], 'tech': []}
    if not projects_dir.exists():
        return docs

    for fn in projects_dir.iterdir():
        if not fn.suffix == '.json':
            continue
        try:
            p = json.loads(fn.read_text(encoding='utf-8'))
        except Exception:
            continue
        name = (p.get('name') or '').lower().strip()
        bucket = None
        if name in _ARCH_PROJECTS:
            bucket = 'arch'
        elif name in _TECH_PROJECTS:
            bucket = 'tech'
        if not bucket:
            continue
        for d in p.get('docs', []):
            content = d.get('content', '').strip()
            if content and len(content) > 100:
                docs[bucket].append({
                    'filename': d.get('filename', f'{name}_doc.txt'),
                    'content': content,
                    'project_name': p.get('name', ''),
                })
    return docs


def _p(text: str) -> None:
    """Print with safe encoding for Windows cp1252 terminals."""
    print(text.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8'))


def print_preview(scored: list[ScoredConv], project_docs: dict) -> None:
    arch = [s for s in scored if s.bucket == 'arch']
    tech = [s for s in scored if s.bucket == 'tech']
    skip = [s for s in scored if s.bucket == 'skip']

    arch_docs = project_docs.get('arch', [])
    tech_docs = project_docs.get('tech', [])

    _p(f"\n{'=' * 68}")
    _p(f"  Claude Export -- Work Filter Results")
    _p(f"{'=' * 68}\n")

    _p(f"[ARCH]  DIGG ARCHITECTURE KB  ->  tenant: digg-demo")
    _p(f"   {len(arch)} conversations + {len(arch_docs)} project docs\n")
    for s in arch:
        _p(f"   [{s.arch_score:3d}]  {s.title[:60]:<60} ({s.message_count} msgs)")
    if arch_docs:
        _p(f"\n   Project docs:")
        for d in arch_docs:
            _p(f"   [doc]  {d['filename']}  [{d['project_name']}]  ({len(d['content'])} chars)")

    _p(f"\n[TECH]  VULA TECH KB  ->  tenant: vula-internal")
    _p(f"   {len(tech)} conversations + {len(tech_docs)} project docs\n")
    for s in tech:
        _p(f"   [{s.tech_score:3d}]  {s.title[:60]:<60} ({s.message_count} msgs)")
    if tech_docs:
        _p(f"\n   Project docs:")
        for d in tech_docs:
            _p(f"   [doc]  {d['filename']}  [{d['project_name']}]  ({len(d['content'])} chars)")

    _p(f"\n[SKIP]  SKIPPING: {len(skip)} conversations (personal / low relevance)")

    arch_msgs = sum(s.message_count for s in arch)
    tech_msgs = sum(s.message_count for s in tech)
    _p(f"\n{'-' * 68}")
    _p(f"  DIGG arch:  {len(arch):3d} convs  {arch_msgs:4d} msgs  +{len(arch_docs)} docs")
    _p(f"  Vula tech:  {len(tech):3d} convs  {tech_msgs:4d} msgs  +{len(tech_docs)} docs")
    _p(f"  Skipped:    {len(skip):3d} convs  (not ingested)")
    _p(f"{'-' * 68}\n")


async def ingest_bucket(items: list[ScoredConv], project_docs: list[dict],
                        tenant_id: str, label: str) -> None:
    from config import settings
    from vula.ingestion.pipeline import VulaIngestionPipeline

    pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
    dest_dir = settings.upload_dir / tenant_id / 'claude_export'
    dest_dir.mkdir(parents=True, exist_ok=True)

    ok = 0

    # Ingest conversations
    for s in items:
        safe = re.sub(r'[^\w\s-]', '', s.title).strip().replace(' ', '_')[:55]
        fp = dest_dir / f"conv_{safe or s.uuid[:8]}.txt"
        fp.write_text(s.formatted_text, encoding='utf-8')
        try:
            result = await pipeline.ingest_file(fp)
            status = 'OK' if result.status in ('done', 'success') else 'FAIL'
            _p(f"  [{status}]  {s.title[:58]:<58} {result.chunks_stored} chunks")
            if result.status in ('done', 'success'):
                ok += 1
        except Exception as exc:
            _p(f"  [ERR]  {s.title[:58]} -- {exc}")

    # Ingest project docs
    for d in project_docs:
        safe = re.sub(r'[^\w\s-]', '', d['filename']).strip().replace(' ', '_')[:55]
        fp = dest_dir / f"doc_{safe}"
        fp.write_text(d['content'], encoding='utf-8')
        try:
            result = await pipeline.ingest_file(fp)
            status = 'OK' if result.status in ('done', 'success') else 'FAIL'
            _p(f"  [{status}]  [DOC] {d['filename'][:55]:<55} {result.chunks_stored} chunks")
            if result.status in ('done', 'success'):
                ok += 1
        except Exception as exc:
            _p(f"  [ERR]  [DOC] {d['filename']} -- {exc}")

    total = len(items) + len(project_docs)
    _p(f"\n  {label}: ingested {ok}/{total} items into tenant '{tenant_id}'")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Claude export into Vula")
    parser.add_argument('export_path', help='Path to export folder or conversations.json')
    parser.add_argument('--dry-run', action='store_true', help='Preview only')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation')
    parser.add_argument('--arch-only', action='store_true', help='DIGG arch only')
    parser.add_argument('--tech-only', action='store_true', help='Vula tech only')
    args = parser.parse_args()

    export_path = Path(args.export_path)

    # Accept folder or direct file
    if export_path.is_dir():
        conv_file = export_path / 'conversations.json'
        projects_dir = export_path / 'projects'
    else:
        conv_file = export_path
        projects_dir = export_path.parent / 'projects'

    if not conv_file.exists():
        print(f"Error: conversations.json not found at {conv_file}")
        sys.exit(1)

    _p(f"\nReading {conv_file.name} ...")
    data = json.loads(conv_file.read_text(encoding='utf-8'))
    conversations = data if isinstance(data, list) else data.get('conversations', [])
    _p(f"  {len(conversations)} total conversations")

    _p(f"Scoring ...")
    scored = score_all(conversations)

    _p(f"Reading project docs ...")
    project_docs = extract_project_docs(projects_dir)

    print_preview(scored, project_docs)

    if args.dry_run:
        _p("Dry run -- nothing ingested.\n")
        return

    arch_items = [s for s in scored if s.bucket == 'arch']
    tech_items = [s for s in scored if s.bucket == 'tech']

    if not arch_items and not tech_items:
        _p("Nothing to ingest.\n")
        return

    if not args.yes:
        answer = input("Proceed with ingestion? [y/N] ").strip().lower()
        if answer != 'y':
            _p("Cancelled.\n")
            return

    if not args.tech_only and arch_items:
        _p(f"\nIngesting DIGG architecture KB (digg-demo) ...\n")
        await ingest_bucket(arch_items, project_docs.get('arch', []), 'digg-demo', 'DIGG arch')

    if not args.arch_only and tech_items:
        _p(f"\nIngesting Vula tech KB (vula-internal) ...\n")
        await ingest_bucket(tech_items, project_docs.get('tech', []), 'vula-internal', 'Vula tech')

    _p("\nDone.\n")


if __name__ == '__main__':
    asyncio.run(main())
