"""AtlasPMS simulator public API with fault injection (W2.2 → W2.3).

AtlasPMS is the clean vendor: OpenAPI 3.1, bearer auth, offset pagination,
clear statuses. Fault switchboard can mutate it to cursor pagination,
rename fields, etc.

This module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from simulators.common.faults import pagination_mode_for_scenario
from simulators.common.helpers import (
    check_auth,
    check_endpoint_removed,
    check_rate_limit_and_transient,
    get_dataset_for_current_state,
    get_pagination_params,
    paginate_cursor,
    paginate_offset,
)
from simulators.common.state import get_state, increment_request_count

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

app = FastAPI(title="AtlasPMS Simulator", openapi_url=None, docs_url=None, redoc_url=None)

# Serve docs/ directory as static files (e.g. /docs/atlaspms-openapi.json)
if DOCS_DIR.is_dir():
    app.mount("/docs", StaticFiles(directory=str(DOCS_DIR)), name="docs")


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return {"status": "ok"}


def _serve_openapi_json() -> JSONResponse:
    for fname in ("atlaspms-openapi.json", "atlas-openapi.json"):
        cand = DOCS_DIR / fname
        if cand.is_file():
            data = json.loads(cand.read_text(encoding="utf-8"))
            return JSONResponse(content=data)
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Atlas OpenAPI docs not found"})


def _serve_openapi_yaml() -> PlainTextResponse:
    for fname in ("atlaspms-openapi.yaml", "atlas-openapi.yaml"):
        cand = DOCS_DIR / fname
        if cand.is_file():
            text = cand.read_text(encoding="utf-8")
            return PlainTextResponse(content=text, media_type="text/yaml")
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Atlas OpenAPI docs not found"})


@app.get("/openapi.json")
def get_openapi_json() -> JSONResponse:
    """Serve clean OpenAPI 3.1 docs (P1.W2.3)."""
    return _serve_openapi_json()


@app.get("/openapi.yaml")
def get_openapi_yaml() -> PlainTextResponse:
    return _serve_openapi_yaml()


@app.get("/spec.json")
def get_spec_json() -> JSONResponse:
    """Alias for /openapi.json."""
    return _serve_openapi_json()


@app.get("/spec.yaml")
def get_spec_yaml() -> PlainTextResponse:
    return _serve_openapi_yaml()


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    state = get_state()
    docs_ok = any((DOCS_DIR / n).is_file() for n in ("atlaspms-openapi.json", "atlaspms-openapi.yaml", "atlas-openapi.json", "atlas-openapi.yaml"))
    checks: dict[str, object] = {
        "default": "ok",
        "scenario": "ok",
        "docs": "ok" if docs_ok else "missing",
    }
    status = "ok" if docs_ok else "degraded"
    return {"status": status, "checks": checks, "scenario": state["scenario_id"]}


@app.get("/properties")
def list_properties(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    # Increment and check rate limit even for properties (counts as request)
    count = increment_request_count()
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization, require_bearer=True)

    dataset = get_dataset_for_current_state()
    return {"data": dataset["properties"]}


@app.get("/reservations")
def list_reservations(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    count = increment_request_count()
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization, require_bearer=True)
    check_endpoint_removed(scenario_id, "reservations")

    dataset = get_dataset_for_current_state()
    data = dataset["reservations"]

    # Optional filter by property_id
    prop_filter = request.query_params.get("property_id")
    if prop_filter:
        data = [r for r in data if str(r.get("property_id")) == prop_filter]

    limit, offset, cursor = get_pagination_params(request)
    mode = pagination_mode_for_scenario(scenario_id, default="offset")

    if mode == "cursor":
        sliced, meta = paginate_cursor(data, cursor, limit, scenario_id, count)
        return {"data": sliced, **meta}
    # offset mode (default for Atlas)
    sliced, meta = paginate_offset(data, limit, offset)
    return {"data": sliced, **meta}


@app.get("/folios")
def list_folios(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    count = increment_request_count()
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization, require_bearer=True)
    check_endpoint_removed(scenario_id, "folios")

    dataset = get_dataset_for_current_state()
    data = dataset["folios"]

    prop_filter = request.query_params.get("property_id")
    if prop_filter:
        data = [r for r in data if str(r.get("property_id")) == prop_filter]
    res_filter = request.query_params.get("reservation_id")
    if res_filter:
        data = [r for r in data if str(r.get("reservation_id")) == res_filter]

    limit, offset, cursor = get_pagination_params(request)
    mode = pagination_mode_for_scenario(scenario_id, default="offset")
    if mode == "cursor":
        sliced, meta = paginate_cursor(data, cursor, limit, scenario_id, count)
        return {"data": sliced, **meta}
    sliced, meta = paginate_offset(data, limit, offset)
    return {"data": sliced, **meta}


@app.get("/folios/{charge_id}")
def get_folio(
    charge_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    count = increment_request_count()
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization, require_bearer=True)
    check_endpoint_removed(scenario_id, "folios")

    dataset = get_dataset_for_current_state()
    for rec in dataset["folios"]:
        if str(rec.get("charge_id")) == charge_id:
            return {"data": rec}
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Folio not found"})


@app.get("/reservations/{reservation_id}")
def get_reservation(
    reservation_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    count = increment_request_count()
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization, require_bearer=True)
    check_endpoint_removed(scenario_id, "reservations")

    dataset = get_dataset_for_current_state()
    for rec in dataset["reservations"]:
        if str(rec.get("reservation_id")) == reservation_id:
            return {"data": rec}
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Reservation not found"})
