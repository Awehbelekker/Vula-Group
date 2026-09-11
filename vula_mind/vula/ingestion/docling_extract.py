"""vula/ingestion/docling_extract.py — Docling (github.com/docling-project/docling) as the
long-tail document extractor.

fitz gives exact text for a clean digital PDF (vula/ingestion/pipeline.py's _parse_pdf_fitz),
and vula/ingestion/payment_notice.py deterministically parses FNB's rigid layout — between them
they cover most of what DIGG/OTH actually receive. What's left is the minority that both miss: a
real invoice or BOQ with a multi-column or merged-cell table, where fitz's raw text-join
interleaves columns into something an LLM then misreads. Docling runs a real layout +
table-structure model (self-hosted, CPU, MIT-licensed — no Azure/Mistral per-page bill) and
exports clean, reading-order-correct Markdown instead.

Deliberately narrow: this is NOT a replacement for fitz on the common path. It's only invoked
from _analyze_document as a second attempt, after the normal extraction has already failed
scan_quality_ok/ungrounded_figures once (see whatsapp.py). OCR is explicitly OFF (do_ocr=False)
— vula already has its own OCR path (local vision / cloud) for genuinely scanned documents;
Docling's job here is table/column *structure*, not reading pixels, so it's cheap: no OCR
engine, no vision-language model, just the layout + table-structure models.

Optional dependency: requirements-docling.txt, not requirements.txt, baked into the Docker
image (models included, see Dockerfile) so a real deploy never hits Hugging Face at runtime.
Any import/environment failure (not installed locally, models not baked into this image)
degrades to returning None — the caller falls straight back to its existing behaviour, the same
fail-open stance as every other best-effort extractor in this codebase.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Union

log = logging.getLogger(__name__)

_converter = None
_unavailable = False  # sticky once a real failure is seen — don't retry a broken import/model
                      # load on every single document; that would turn a missing dependency
                      # into a many-second tax on every failing-extraction retry.


def _build_converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions(artifacts_path=os.environ.get("DOCLING_ARTIFACTS_PATH") or None)
    opts.do_ocr = False                    # vula's own OCR path handles genuine scans
    opts.do_table_structure = True         # the actual reason this module exists
    opts.generate_page_images = False
    opts.do_picture_classification = False
    opts.do_code_enrichment = False
    opts.do_formula_enrichment = False
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def _get_converter():
    global _converter, _unavailable
    if _unavailable:
        return None
    if _converter is None:
        try:
            _converter = _build_converter()
        except Exception as exc:
            log.info("Docling unavailable (%s) — long-tail extraction stays on fitz text", exc)
            _unavailable = True
            return None
    return _converter


def _convert_sync(path: str) -> Optional[str]:
    conv = _get_converter()
    if conv is None:
        return None
    try:
        result = conv.convert(path)
        md = (result.document.export_to_markdown() or "").strip()
        return md or None
    except Exception as exc:
        log.warning("Docling conversion failed for %s: %s", path, exc)
        return None


async def extract_markdown(path: Union[str, Path]) -> Optional[str]:
    """Reading-order Markdown (tables preserved) for the PDF at `path`, or None if Docling
    isn't installed, its models aren't available, or the conversion itself failed. CPU-bound
    (roughly a second a page once the models are warm, longer on the very first call in a
    fresh process) — always run off the event loop, never inline in a webhook response."""
    if Path(path).suffix.lower() != ".pdf":
        return None
    return await asyncio.to_thread(_convert_sync, str(path))
