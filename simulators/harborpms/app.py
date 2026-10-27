"""HarborPMS simulator public API with fault injection (W2.2 → W2.4).

HarborPMS is the messy vendor: Markdown docs, cursor pagination with a
repeated-cursor bug, nested folios, opaque status codes, property-local
dates, ambiguous ``date`` field. For W2.2 we implement the cursor
pagination shape and fault-aware mutations; W2.4 adds the full narrative
docs quirks.
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

app = FastAPI(title="HarborPMS Simulator")


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

    prop_filter = request.query_params.get("property_id")
    if prop_filter:
        data = [r for r in data if str(r.get("property_id")) == prop_filter]

    limit, offset, cursor = get_pagination_params(request)
    # Harbor default is cursor (even without fault, it uses cursor)
    mode = pagination_mode_for_scenario(scenario_id, default="cursor")

    if mode == "cursor":
        sliced, meta = paginate_cursor(data, cursor, limit, scenario_id, count)
        return {"data": sliced, **meta}
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
    mode = pagination_mode_for_scenario(scenario_id, default="cursor")
    if mode == "cursor":
        sliced, meta = paginate_cursor(data, cursor, limit, scenario_id, count)
        # Harbor folios are nested under reservation in W2.4; for W2.2 keep flat
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


@app.get("/folios/{charge_id}")
def get_folio(
    charge_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    count = increment_request_count()
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization)
    check_endpoint_removed(scenario_id, "folios")

    dataset = get_dataset_for_current_state()
    for rec in dataset["folios"]:
        if str(rec.get("charge_id")) == charge_id:
            return {"data": rec}
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Folio charge not found"})
