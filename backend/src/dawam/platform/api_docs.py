"""The interactive API docs (Swagger UI) page, under a policy of its own.

Swagger UI is loaded from a CDN and bootstrapped by an inline script, neither of
which the app-wide ``APP_CSP`` allows. Instead of loosening that policy, this page
sends its own: nothing by default, the CDN for scripts and styles, and the inline
script allowed by its hash (never ``'unsafe-inline'``). ``SecurityHeadersMiddleware``
keeps a policy a response already has and adds the other headers.

Swagger UI's online validator is turned off, so the spec is never sent elsewhere.
"""

from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

SWAGGER_JS_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"
SWAGGER_CSS_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"
SWAGGER_FAVICON_URL = "https://fastapi.tiangolo.com/img/favicon.png"

_INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _hash_source(script: str) -> str:
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return f"'sha256-{digest}'"


def _docs_csp(html: str) -> str:
    hashes = [_hash_source(script) for script in _INLINE_SCRIPT.findall(html)]
    return "; ".join(
        [
            "default-src 'none'",
            " ".join(["script-src", _origin(SWAGGER_JS_URL), *hashes]),
            f"style-src {_origin(SWAGGER_CSS_URL)}",
            f"img-src 'self' data: {_origin(SWAGGER_FAVICON_URL)}",
            "connect-src 'self'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        ]
    )


def install_api_docs(app: FastAPI, path: str) -> None:
    """Serve Swagger UI for ``app``'s OpenAPI spec at ``path`` (use with ``docs_url=None``).

    The page and its policy are built once, here: the page links the spec under the
    app's ``root_path``, which is fixed when the app is built.
    """
    if not app.openapi_url:
        raise ValueError("install_api_docs needs an app that publishes its spec (openapi_url)")
    page = get_swagger_ui_html(
        openapi_url=app.root_path.rstrip("/") + app.openapi_url,
        title=f"{app.title} - Swagger UI",
        swagger_js_url=SWAGGER_JS_URL,
        swagger_css_url=SWAGGER_CSS_URL,
        swagger_favicon_url=SWAGGER_FAVICON_URL,
        swagger_ui_parameters={"validatorUrl": None},
    )
    html = bytes(page.body).decode()
    headers = {"Content-Security-Policy": _docs_csp(html)}

    async def api_docs(request: Request) -> HTMLResponse:
        return HTMLResponse(html, headers=headers)

    app.add_route(path, api_docs, include_in_schema=False)
