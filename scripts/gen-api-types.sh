#!/usr/bin/env bash
# Regenerate TypeScript API types from the FastAPI OpenAPI schema.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m mind_api.cli openapi > packages/shared-types/openapi.json
pnpm --filter @mind/shared-types exec openapi-typescript openapi.json --default-non-nullable false -o src/openapi.ts
echo "Generated packages/shared-types/src/openapi.ts"
