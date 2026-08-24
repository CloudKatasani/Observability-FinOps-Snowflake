"""Single-port ASGI composition for the all-in-one image (R10).

The three-container topology puts nginx in front of the SPA and proxies ``/api``
to the API. The all-in-one image has no nginx, so the same job is done here: the
built SPA is mounted under the API application, which makes the API and the SPA
literally same-origin and removes the proxy hop.

This is a *deployment adapter*, not application code. It adds no behaviour to
the API — it only decides who serves the static bundle — which is exactly the
kind of difference R10 allows to live outside the application.

The SPA is a history-mode single-page app: a deep link such as ``/chargeback``
is a client route with no file behind it, so a miss on a browser navigation
falls back to ``index.html``. API paths are registered before this mount, so a
request for an unknown ``/api`` route still gets the API's own 404 rather than
the app shell.

Served by uvicorn as a factory::

    uvicorn allinone.asgi:create_app --factory --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

#: Where the Dockerfile puts the built SPA, and where `make demo-native` puts
#: it when the same adapter is served from a checkout. Resolved by looking for
#: the bundle rather than read from the environment, so §21's "the settings
#: module is the only place environment variables are read" still holds.
_IMAGE_WEB_ROOT = Path("/app/web")
_CHECKOUT_WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web" / "dist"
DEFAULT_WEB_ROOT = (
    _IMAGE_WEB_ROOT if (_IMAGE_WEB_ROOT / "index.html").is_file() else _CHECKOUT_WEB_ROOT
)


class SpaFiles(StaticFiles):
    """Static files with a single-page-app history fallback.

    ``reserved`` holds the first path segment of every route the API itself
    owns. A miss under one of those is the API's 404 and must stay a 404 —
    answering ``GET /api/v1/nope`` with the app shell would turn a client bug
    into a blank page and a support ticket.
    """

    def __init__(self, *, directory: Path, reserved: frozenset[str]) -> None:
        super().__init__(directory=directory, html=True)
        self.reserved = reserved

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or scope.get("method") not in ("GET", "HEAD"):
                raise
            if path.split("/", 1)[0] in self.reserved:
                raise
            return await super().get_response("index.html", scope)


def api_owned_prefixes(app: FastAPI) -> frozenset[str]:
    """First path segment of every route the API registers.

    Derived from the application rather than hardcoded, so a router added in
    the API keeps its own 404s without anyone remembering to edit this file.
    The OpenAPI schema is the reliable source for router-mounted paths (the
    application object stores included routers opaquely); the route list adds
    the documentation endpoints, which the schema does not describe.
    """
    paths: set[str] = set(app.openapi().get("paths", {}))
    for route in app.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)

    prefixes: set[str] = set()
    for path in paths:
        if not path.startswith("/") or path == "/":
            continue
        head = path.split("/")[1]
        if head and not head.startswith("{"):
            prefixes.add(head)
    return frozenset(prefixes)


def create_app(web_root: Path = DEFAULT_WEB_ROOT) -> FastAPI:
    """The API application with the built SPA mounted at the root."""
    from snowobs_api.main import create_app as create_api_app

    if not (web_root / "index.html").is_file():
        raise RuntimeError(
            f"No SPA bundle at {web_root}. In the all-in-one image it is built by "
            "the web stage (check the Dockerfile's COPY of apps/web/dist); from a "
            "checkout, build it with: cd apps/web && npm run build."
        )
    app = create_api_app()
    app.mount("/", SpaFiles(directory=web_root, reserved=api_owned_prefixes(app)), name="spa")
    return app
