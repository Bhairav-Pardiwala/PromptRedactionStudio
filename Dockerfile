# Prompt Redaction Studio
#
# Build (default -- includes both spaCy models, ~1.6 GB image):
#   docker build -t prompt-redaction .
#
# Build a slim image with only the small model (~700 MB, weaker name detection):
#   docker build --build-arg INCLUDE_LARGE_MODEL=false -t prompt-redaction:slim .
#
# Run:
#   docker run --rm -p 8000:8000 prompt-redaction
#
# The transformers engine is deliberately not installed: torch plus the NER weights add
# roughly 2 GB. Uncomment the block near the bottom if you want it baked in.

FROM python:3.13-slim AS base

ARG INCLUDE_LARGE_MODEL=true

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so edits to application code do not invalidate this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# spaCy models are their own layer for the same reason. en_core_web_lg is the slow,
# heavy one; INCLUDE_LARGE_MODEL=false skips it and the app falls back to the small
# model, which the UI reports as the only available engine.
RUN python -m spacy download en_core_web_sm && \
    if [ "$INCLUDE_LARGE_MODEL" = "true" ]; then \
        python -m spacy download en_core_web_lg; \
    else \
        echo "Skipping en_core_web_lg (INCLUDE_LARGE_MODEL=$INCLUDE_LARGE_MODEL)"; \
    fi

# Optional transformers engine. Uncomment both lines to include it (~2 GB extra).
# RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
# RUN pip install --no-cache-dir "presidio-analyzer[transformers]"

COPY app/ ./app/

# Run as a non-root user. Nothing in the app writes to disk -- redaction mappings live
# in memory only -- so the filesystem can stay owned by root.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# 0.0.0.0 rather than 127.0.0.1, otherwise the port publish cannot reach the server.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
