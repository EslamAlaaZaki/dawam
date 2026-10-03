"""The frontend's committed OpenAPI spec matches the app (the TS client is generated from it).

CI additionally regenerates the TypeScript client from it (scripts/check-api-client.sh).
"""

from pathlib import Path

from dawam.__main__ import openapi_spec

COMMITTED_SPEC = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "openapi.json"


def test_committed_openapi_spec_is_current():
    assert COMMITTED_SPEC.read_text(encoding="utf-8") == openapi_spec(), (
        "frontend/src/api/openapi.json is stale. Run scripts/generate-api-client.sh "
        "(or: cd backend && python -m dawam openapi --output ../frontend/src/api/openapi.json, "
        "then cd frontend && npm run generate:api) and commit the result."
    )
