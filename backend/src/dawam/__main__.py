"""Command line entry point: ``python -m dawam {serve,worker,openapi}``.

- ``serve``: run the API (and the built frontend, if ``DAWAM_FRONTEND_DIST`` is set).
- ``worker``: run the background worker process.
- ``openapi``: print or write the OpenAPI spec the frontend client is generated from.

Configuration comes from ``DAWAM_*`` environment variables (see ``.env.example``).
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from dawam.platform.config import Settings

# The spec never touches the database; the URL only satisfies Settings.
_OPENAPI_PLACEHOLDER_DB = "postgresql+psycopg://openapi@localhost/openapi"


def openapi_spec() -> str:
    from dawam.app import create_app

    app = create_app(Settings(database_url=_OPENAPI_PLACEHOLDER_DB))
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from dawam.app import create_app
    from dawam.platform.logs import configure_logging

    settings = Settings()  # type: ignore[call-arg]
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        log_config=None,
        access_log=False,  # RequestContextMiddleware logs each request with its id
        proxy_headers=True,
    )
    return 0


def _worker(args: argparse.Namespace) -> int:
    from dawam.platform.logs import configure_logging
    from dawam.worker import run_worker

    settings = Settings()  # type: ignore[call-arg]
    configure_logging(settings.log_level)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    run_worker(settings, stop=stop)
    return 0


def _openapi(args: argparse.Namespace) -> int:
    spec = openapi_spec()
    if args.output:
        Path(args.output).write_text(spec, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(spec)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dawam")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=_serve)

    worker = commands.add_parser("worker", help="run the background worker")
    worker.set_defaults(handler=_worker)

    openapi = commands.add_parser("openapi", help="export the OpenAPI spec")
    openapi.add_argument("--output", help="file to write (default: stdout)")
    openapi.set_defaults(handler=_openapi)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
