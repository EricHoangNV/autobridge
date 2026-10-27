"""AtlasPMS simulator public API with fault injection (W2.2 → W2.3).

AtlasPMS is the clean vendor: OpenAPI 3.1, bearer auth, offset pagination,
clear statuses. Fault switchboard can mutate it to cursor pagination,
rename fields, etc.

This module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

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

app = FastAPI(title="AtlasPMS Simulator")


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    state = get_state()
    return {
        "status": "ok",
        "checks": {
            "default": "ok",
            "scenario": state["scenario_id"],
        },
    }


@app.get("/properties")
def list_properties(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    # Increment and check rate limit even for properties (counts as request)
    count = increment_request_count()
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization)

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
    check_auth(scenario_id, authorization)
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
    check_auth(scenario_id, authorization)
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


@app.get("/reservations/{reservation_id}")
def get_reservation(
    reservation_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    count = increment_request_count()
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization)
    check_endpoint_removed(scenario_id, "reservations")

    dataset = get_dataset_for_current_state()
    for rec in dataset["reservations"]:
        if str(rec.get("reservation_id")) == reservation_id:
            return {"data": rec}
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Reservation not found"})
