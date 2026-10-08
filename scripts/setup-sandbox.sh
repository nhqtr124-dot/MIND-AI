#!/usr/bin/env bash
# Build/pull the sandbox runtime images and create the internal preview network.
set -euo pipefail
cd "$(dirname "$0")/.."

docker info >/dev/null 2>&1 || { echo "Docker daemon is not reachable; MIND Builder execution will be disabled." >&2; exit 1; }

args=()
if [[ -n "${HTTPS_PROXY:-}" ]]; then
  args+=(--network host --build-arg "HTTPS_PROXY=${HTTPS_PROXY}" --build-arg "https_proxy=${HTTPS_PROXY}")
fi
ca="${MIND_BUILD_CA_BUNDLE:-${SSL_CERT_FILE:-}}"
if [[ -n "$ca" && -f "$ca" ]]; then
  args+=(--secret "id=ca,src=$ca")
fi

docker build "${args[@]}" -t mind-sandbox-python:latest infrastructure/docker/sandbox-python
docker pull node:22-alpine
docker pull python:3.12-slim
docker network inspect mind-sandbox-net >/dev/null 2>&1 || docker network create --internal --label mind.sandbox=1 mind-sandbox-net
echo "Sandbox images and network ready."
