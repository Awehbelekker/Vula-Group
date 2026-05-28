FROM python:3.11-slim

WORKDIR /app

# System deps for document parsing and OCR
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first — cached layer, only rebuilds when requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent data directory — mount a Railway volume at /data
ENV DATA_DIR=/data
ENV UPLOAD_DIR=/data/uploads
ENV TAKEOFF_UPLOAD_DIR=/data/takeoff

RUN mkdir -p /data/uploads /data/takeoff

EXPOSE 7438

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:7438/status || exit 1

CMD ["uvicorn", "vula.api.server:app", "--host", "0.0.0.0", "--port", "7438", "--workers", "2"]
