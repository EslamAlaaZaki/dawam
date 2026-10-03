#!/usr/bin/env sh
# Regenerate the frontend API client from the backend's OpenAPI spec.
# Needs the backend installed (pip install -e backend) and frontend deps (npm ci).
# Set PYTHON to choose the interpreter (default: python).
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root/backend"
"${PYTHON:-python}" -m dawam openapi --output ../frontend/src/api/openapi.json
cd "$root/frontend"
npm run --silent generate:api
