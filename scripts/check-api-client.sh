#!/usr/bin/env sh
# Fail if the committed frontend API client differs from what the backend generates.
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"
"$root/scripts/generate-api-client.sh"
cd "$root"
if [ -n "$(git status --porcelain -- frontend/src/api)" ]; then
  git --no-pager diff -- frontend/src/api
  echo "The API client in frontend/src/api is out of date." >&2
  echo "Run scripts/generate-api-client.sh and commit the result." >&2
  exit 1
fi
echo "API client is up to date."
