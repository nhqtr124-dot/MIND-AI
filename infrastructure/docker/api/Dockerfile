# syntax=docker/dockerfile:1
# MIND API / worker / preview-proxy image (same image, different commands).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
# libgl/libxrender: OpenCascade (CadQuery); fonts: PDF rendering incl. Arabic; docker CLI: sandbox control.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 libxrender1 libgomp1 fonts-dejavu-core docker.io curl \
 && rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir "uv==0.11.32"
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY modules ./modules
COPY services/api ./services/api
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    uv sync --frozen --no-dev --package mind-api
ENV PATH="/app/.venv/bin:$PATH" MIND_STORAGE_DIR=/data/storage
WORKDIR /app/services/api
EXPOSE 8000 8100
CMD ["uvicorn", "mind_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
