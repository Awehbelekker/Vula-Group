"""
core/skills/file_parse.py

File parsing skill — uses the ingestion pipeline to extract and
analyse document content. Handles PDFs, Word docs, Excel, images.

Triggered when a file path or URL is provided, or keywords like
"read", "parse", "summarise this document", "from this file".
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from core.skills.base import BaseSkill, SkillInput, SkillOutput

logger = logging.getLogger(__name__)

_SUPPORTED_EXT = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".csv", ".png", ".jpg", ".jpeg"}


class FileParseSkill(BaseSkill):
    name = "file_parse"
    description = "Reads, extracts and analyses documents and files using the Vula ingestion pipeline"

    async def run(self, inp: SkillInput) -> SkillOutput:
        # Extract file path or URL from question or metadata
        file_path = inp.metadata.get("file_path")
        question = inp.question

        # Try to find a file path in the question text
        if not file_path:
            path_match = re.search(
                r'(?:[A-Za-z]:\\|/)[^\s"\'<>|?*]+\.(?:pdf|docx?|xlsx?|txt|csv|png|jpe?g)',
                question, re.I
            )
            if path_match:
                file_path = path_match.group(0)

        if not file_path:
            # No file — search KB for matching documents
            return await self._search_kb(inp)

        path = Path(file_path)
        if not path.exists():
            return SkillOutput(
                answer=f"File not found: {file_path}",
                skill_name=self.name,
                confidence=0.0,
                error="FileNotFoundError",
            )

        if path.suffix.lower() not in _SUPPORTED_EXT:
            return SkillOutput(
                answer=f"Unsupported file type: {path.suffix}. Supported: {', '.join(_SUPPORTED_EXT)}",
                skill_name=self.name,
                confidence=0.0,
                error="UnsupportedFileType",
            )

        try:
            from vula.ingestion.pipeline import VulaIngestionPipeline
            pipeline = VulaIngestionPipeline(tenant_id=inp.tenant_id)
            result = await pipeline.ingest_file(path)

            if result.status not in ("success", "done") or not result.chunks_stored:
                return SkillOutput(
                    answer=f"Could not extract content from {path.name}: {result.error or 'unknown error'}",
                    skill_name=self.name,
                    confidence=0.2,
                    error=result.error,
                )

            # Now query the freshly ingested content
            chunks = await pipeline.query(question, top_k=5)
            if not chunks:
                return SkillOutput(
                    answer=f"File ingested ({result.chunks_stored} chunks) but no relevant content found for: {question}",
                    skill_name=self.name,
                    confidence=0.4,
                    sources=[{"type": "file", "filename": path.name, "chunks": result.chunks_stored}],
                )

            context = "\n\n".join(c.get("text", "")[:500] for c in chunks)
            return SkillOutput(
                answer=context,
                skill_name=self.name,
                confidence=0.85,
                sources=[{
                    "type": "file",
                    "filename": path.name,
                    "chunks_stored": result.chunks_stored,
                    "pages": result.pages_processed,
                }],
            )

        except Exception as exc:
            logger.error("File parse skill failed for %s: %s", file_path, exc)
            return SkillOutput(
                answer=f"Failed to parse {Path(file_path).name}: {exc}",
                skill_name=self.name,
                confidence=0.0,
                error=str(exc),
            )

    async def _search_kb(self, inp: SkillInput) -> SkillOutput:
        """No file provided — search tenant KB and SYNTHESISE a clean answer."""
        try:
            from vula.ingestion.pipeline import VulaIngestionPipeline
            pipeline = VulaIngestionPipeline(tenant_id=inp.tenant_id)
            chunks = await pipeline.query(inp.question, top_k=inp.top_k)
            if not chunks:
                return SkillOutput(
                    answer="I don't have anything on that in your documents yet.",
                    skill_name=self.name,
                    confidence=0.1,
                )
            # Synthesise a real answer from the chunks (not raw text dump)
            answer = await pipeline.answer(
                inp.question,
                context_label="business documents",
                conversation_history=inp.conversation_history,
            )
            return SkillOutput(
                answer=answer,
                skill_name=self.name,
                confidence=min(0.9, len(chunks) * 0.18),
                sources=[{"type": "kb", "filename": c.get("filename", "?"),
                           "score": round(c.get("score", 0.0), 3)} for c in chunks],
            )
        except Exception as exc:
            return SkillOutput(
                answer=f"KB search failed: {exc}",
                skill_name=self.name,
                confidence=0.0,
                error=str(exc),
            )
