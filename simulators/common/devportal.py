"""Vendor developer portal pages for the simulators (demo presentation).

Each simulated vendor gets a human-readable docs site at ``/developers``,
styled as that vendor's own online documentation. The page is static HTML
from ``simulators/devportal/``; it renders the real source document at load
time, fetched from ``/developers/source`` (Markdown as text, the OpenAPI YAML
parsed to JSON). The raw files stay served by the existing ``/docs`` mount,
which is what AutoBridge's docs reader uses.

This module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

PORTAL_DIR = Path(__file__).resolve().parent.parent / "devportal"
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

# Shared renderer scripts and styles the pages may load; anything else is 404.
ASSETS = {
    "markdown.js": "text/javascript",
    "openapi.js": "text/javascript",
    "banner.css": "text/css",
}


def _source_path(source: str) -> Path:
    path = DOCS_DIR / source
    if not path.is_file():
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": f"{source} not found"}
        )
    return path


def mount_devportal(app: FastAPI, *, page: str, source: str) -> None:
    """Add ``/developers`` (page), ``/developers/source`` and ``/developers/assets/{name}``.

    ``page`` is the HTML file in ``simulators/devportal``; ``source`` is the
    document in ``simulators/docs`` the page renders (``.md`` or ``.yaml``).
    """

    @app.get("/developers", include_in_schema=False)
    def get_developers() -> HTMLResponse:
        return HTMLResponse((PORTAL_DIR / page).read_text(encoding="utf-8"))

    @app.get("/developers/source", include_in_schema=False, response_model=None)
    def get_developers_source() -> PlainTextResponse | JSONResponse:
        path = _source_path(source)
        text = path.read_text(encoding="utf-8")
        if path.suffix in (".yaml", ".yml"):
            return JSONResponse(yaml.safe_load(text))
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")

    @app.get("/developers/assets/{name}", include_in_schema=False)
    def get_developers_asset(name: str) -> FileResponse:
        if name not in ASSETS:
            raise HTTPException(
                status_code=404, detail={"code": "not_found", "message": "asset not found"}
            )
        return FileResponse(PORTAL_DIR / name, media_type=ASSETS[name])
