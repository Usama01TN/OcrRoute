FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OCRROUTE_HOME=/data OCRROUTE_HOST=0.0.0.0 OCRROUTE_PORT=20256
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY ocrroute ./ocrroute
COPY AioOCR ./AioOCR
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir ".[local]"
VOLUME ["/data"]
EXPOSE 20256
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:20256/v1/health').status==200 else 1)"
CMD ["ocrroute", "serve", "--host", "0.0.0.0", "--port", "20256"]
