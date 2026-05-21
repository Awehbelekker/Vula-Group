FROM python:3.11-slim

WORKDIR /app

# System deps for document parsing and OCR
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 7438

CMD ["uvicorn", "vula.api.server:app", "--host", "0.0.0.0", "--port", "7438"]
