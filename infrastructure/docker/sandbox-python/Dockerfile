# syntax=docker/dockerfile:1
# Runtime image for MIND Builder Python projects and one-off code execution.
# Common web dependencies are preinstalled because sandboxed runs have no network.
FROM python:3.12-slim
# An optional "ca" build secret lets builds work behind TLS-intercepting proxies.
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir "fastapi>=0.115" "uvicorn>=0.30" "jinja2>=3.1" "sqlalchemy>=2.0" "pydantic>=2.9" "pytest>=8" "httpx>=0.27" "numpy>=2" \
 && useradd --uid 10001 --create-home sandbox
USER 10001
WORKDIR /workspace
