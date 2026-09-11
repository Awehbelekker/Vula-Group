# Root Dockerfile — Railway builds this repo from the REPOSITORY ROOT as the build context
# (there is no per-service "root directory" set, and railway.toml's buildContext key is not a
# real Railway field, so it was silently ignored — every deploy since 2026-09-02 failed here on
# `COPY requirements.txt` because that file is under vula_mind/).
#
# This file mirrors vula_mind/Dockerfile exactly, but every COPY is prefixed with vula_mind/ so
# it works from the repo root. vula_mind/Dockerfile stays as-is for local `docker build` run
# from inside vula_mind/.

FROM python:3.11-slim

WORKDIR /app

# System deps for document parsing, OCR, and PDF generation (WeasyPrint)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libmagic1 \
    curl \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first — cached layer, only rebuilds when requirements.txt changes
COPY vula_mind/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Docling — self-hosted long-tail document extractor (vula/ingestion/docling_extract.py).
# Separate requirements file: its torch dependency resolves against PyTorch's own CPU wheel
# index and shouldn't influence the main requirements.txt resolution.
COPY vula_mind/requirements-docling.txt .
RUN pip install --no-cache-dir -r requirements-docling.txt

# Bake its layout + table-structure models into the image at BUILD time — not on the first
# real WhatsApp document, and not a runtime dependency on Hugging Face being reachable from
# Railway. Only the two models the pipeline actually uses (do_ocr=False, no code/formula/
# picture enrichment) — skips several hundred MB of OCR/vision-language models Docling
# downloads by default.
RUN python -c "from docling.utils.model_downloader import download_models; \
download_models(with_layout=True, with_tableformer=True, with_code_formula=False, \
with_picture_classifier=False, with_rapidocr=False)"
ENV DOCLING_ARTIFACTS_PATH=/root/.cache/docling/models

COPY vula_mind/ .

# Persistent data directory — mount a Railway volume at /data
ENV DATA_DIR=/data
ENV UPLOAD_DIR=/data/uploads
ENV TAKEOFF_UPLOAD_DIR=/data/takeoff

RUN mkdir -p /data/uploads /data/takeoff

EXPOSE 7438

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:7438/status || exit 1

CMD ["python", "start.py"]
