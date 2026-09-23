"""
vula/ingestion/pdf_extract.py — PyMuPDF text/table extraction, run in a child process.

2026-09-23: every ~3.5 min since 2026-09-22 a uvicorn worker on Railway died ~20-30 s after
"Ingesting: HPC01_JORDAAN_STREET_DRAWING_PACK_COUNCIL_SUBMISSION_REV_4_230926.pdf" (digg-demo,
an emailed architectural drawing pack). PyMuPDF's C calls (get_text / get_drawings /
find_tables / get_pixmap on sheets carrying tens of thousands of vector paths) ran on the
worker's event loop and held the GIL long enough that uvicorn's 5 s worker health check failed
and the worker was killed. Every in-flight WhatsApp turn on it died too, and because email sync
only saves its cursor after a whole batch, the same email was re-fetched and the worker killed
again on every sweep — stalling mail sync for every tenant.

Running the extraction here, in its own interpreter with a hard timeout, means a pathological
PDF can only ever cost this child process: a hang is killed, a crash is an exit code, and the
web worker's GIL and event loop are never involved.

Usage (internal): python -m vula.ingestion.pdf_extract <pdf> <render_dir>
Prints one JSON object as its last stdout line: {"pages": [[page_no, text, png_path_or_null], ...]}. A page with no
usable text layer but some ink is rendered to <render_dir> for the parent to OCR (OCR is async
and routed, so it stays in the parent).
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

# Pages bigger than A3 are drawing sheets (A2/A1/A0 plans), not invoices/statements. Their
# table finder walks every vector path on the sheet and can take minutes; the tables on them
# (title blocks, schedules) are already in the plain text layer.
_TABLE_MAX_AREA_PT2 = 842 * 1191 * 1.05


def extract(pdf: str, render_dir: str) -> list:
    import pymupdf  # aka fitz

    out = []
    with pymupdf.open(pdf) as doc:
        for i in range(doc.page_count):
            page = doc.load_page(i)
            text = (page.get_text("text") or "").strip()
            png = None
            if len(text) < 50:
                # No usable text layer — is there ink to OCR? (a truly blank page: skip)
                if page.get_images() or page.get_drawings():
                    png = str(Path(render_dir) / f"vula_fitz_{uuid.uuid4().hex}.png")
                    page.get_pixmap(dpi=200).save(png)
            elif page.rect.width * page.rect.height <= _TABLE_MAX_AREA_PT2:
                # Tables (pymupdf >= 1.23) — appended, best-effort.
                try:
                    for tbl in page.find_tables().tables:
                        rows = ["  ".join(str(c) for c in row if c) for row in tbl.extract() if any(row)]
                        if rows:
                            text += "\n\n" + "\n".join(rows)
                except Exception:
                    pass
            if text or png:
                out.append([i + 1, text, png])
    return out


if __name__ == "__main__":
    result = {"pages": extract(sys.argv[1], sys.argv[2])}
    sys.stdout.write("\n" + json.dumps(result) + "\n")
