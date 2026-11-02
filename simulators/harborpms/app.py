"""HarborPMS simulator public API with fault injection (W2.2 → W2.4).

HarborPMS is the messy vendor: Markdown narrative docs, cursor pagination with a
repeated-cursor bug, nested folios, opaque status codes, property-local
dates, ambiguous ``date`` field.

Quirks implemented here (P1.W2.4):
- Opaque status codes: canonical ``confirmed`` → ``H1`` etc. (see HARBOR_STATUS_MAP)
- Ambiguous ``date`` field: ``date`` duplicates ``arrival_date`` (reservations)
  and ``business_date`` (folios); docs call it "the date" without saying which.
- Nested folios: ``GET /reservations/{id}/folios`` returns folios for that
  reservation (cursor paginated); the flat ``GET /folios`` exists but the
  nested endpoint is intentionally undocumented in the Markdown guide.
- Cursor pagination with repeated-cursor bug via fault switchboard (same helper
  as Atlas).
- Markdown docs served from ``simulators/docs/harbor.md`` (and ``harborpms.md``
  alias) via static mount; readyz checks docs presence.
- One wrong example in the Markdown (offset pagination example while reality is
  cursor; status example shows Atlas English while reality is H1..H5).
- One undocumented endpoint: ``GET /reservations/{id}/folios`` not in the guide.

This module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from simulators.common.devportal import mount_devportal
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

# Opaque status vocabulary for HarborPMS (baseline, before faults)
HARBOR_STATUS_MAP: dict[str, str] = {
    "confirmed": "H1",
    "checked_in": "H2",
    "checked_out": "H3",
    "cancelled": "H4",
    "no_show": "H5",
}
REVERSE_HARBOR_STATUS_MAP: dict[str, str] = {v: k for k, v in HARBOR_STATUS_MAP.items()}

app = FastAPI(title="HarborPMS Simulator", openapi_url=None, docs_url=None, redoc_url=None)

# Serve docs/ directory as static files (e.g. /docs/harbor.md)
if DOCS_DIR.is_dir():
    app.mount("/docs", StaticFiles(directory=str(DOCS_DIR)), name="docs")

# Human-readable vendor docs site (demo presentation); renders harbor.md at load time.
mount_devportal(app, page="harbor.html", source="harbor.md")


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return {"status": "ok"}


def _harbor_readyz_docs_ok() -> bool:
    return any(
        (DOCS_DIR / n).is_file()
        for n in ("harbor.md", "harborpms.md", "harbor.txt", "harborpms-openapi.json")
    )


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    state = get_state()
    docs_ok = _harbor_readyz_docs_ok()
    checks: dict[str, object] = {
        "default": "ok",
        "scenario": "ok",
        "docs": "ok" if docs_ok else "missing",
    }
    status = "ok" if docs_ok else "degraded"
    return {"status": status, "checks": checks, "scenario": state["scenario_id"]}


# ---------------------------------------------------------------------------
# Harbor-specific record transforms
# ---------------------------------------------------------------------------


def _split_nesting(rec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Unwrap beacon-nested internal records (REQ-083 D17).

    A nesting mutation wraps the internal record as ``{parent: {...}}``
    (possibly several levels). The vendor projects the inner record and
    re-applies the same parents to the wire record — as a real vendor
    would — so quirks (H1 mapping, ``date`` dup) survive under nesting
    and a path patch can repair it. Returns ``(inner, parents)``; a flat
    record returns ``(rec, [])``.
    """
    inner = rec if isinstance(rec, dict) else {}
    parents: list[str] = []
    for _ in range(5):
        if not isinstance(inner, dict):
            break
        if any(
            k in inner
            for k in (
                "reservation_id",
                "property_id",
                "charge_id",
                "arrival_date",
                "departure_date",
                "business_date",
                "status",
                "guest_name",
                "total_amount",
                "amount",
            )
        ):
            break
        if len(inner) == 1:
            _k, _v = next(iter(inner.items()))
            if isinstance(_v, dict) and isinstance(_k, str) and _k:
                parents.append(_k)
                inner = _v
                continue
        break
    return (inner if isinstance(inner, dict) else {}, parents)


def _wrap_wire(wire: dict[str, Any], parents: list[str]) -> dict[str, Any]:
    """Re-apply nesting parents to a projected wire record."""
    out = wire
    for parent in reversed(parents):
        out = {parent: out}
    return out


def _inner_get(rec: Any, key: str, default: Any = None) -> Any:
    """Internal field read that survives a nesting wrap (REQ-084)."""
    # (_split_nesting is total over any input: no fallback needed.)
    inner, _ = _split_nesting(rec if isinstance(rec, dict) else {})
    if isinstance(inner, dict) and key in inner:
        return inner[key]
    return default


def _wire_get(wire: Any, key: str, default: Any = None) -> Any:
    """Wire leaf read that survives a nesting wrap (REQ-084)."""
    node = wire
    for _ in range(6):
        if isinstance(node, dict) and key in node:
            return node[key]
        if isinstance(node, dict) and len(node) == 1:
            _k, _v = next(iter(node.items()))
            if isinstance(_v, dict) and isinstance(_k, str) and _k:
                node = _v
                continue
        return default
    return default


def _to_harbor_reservation(rec: dict[str, Any]) -> dict[str, Any]:
    """Map a canonical reservation record to Harbor's messy shape."""
    # REQ-083 D17: nest the projected wire record, not the internal one.
    _parents: list[str] = []
    rec, _parents = _split_nesting(rec if isinstance(rec, dict) else {})
    out = dict(rec)
    # Opaque status: map canonical -> H1..H5 if present in map (leave fault-injected vocab alone)
    status = out.get("status")
    if isinstance(status, str) and status in HARBOR_STATUS_MAP:
        out["status"] = HARBOR_STATUS_MAP[status]
    # Ambiguous `date` field: duplicates arrival_date value; docs call it "the date"
    # Keep arrival_date canonical, add duplicate `date` for ambiguity.
    if "arrival_date" in out:
        out["date"] = out["arrival_date"]
    if _parents:
        return _wrap_wire(out, _parents)
    return out


def _to_harbor_folio(rec: dict[str, Any]) -> dict[str, Any]:
    """Map a canonical folio record to Harbor's messy shape."""
    _parents: list[str] = []
    rec, _parents = _split_nesting(rec if isinstance(rec, dict) else {})
    out = dict(rec)
    # Folios have no status to map
    if "business_date" in out:
        out["date"] = out["business_date"]
    if _parents:
        return _wrap_wire(out, _parents)
    return out


def _harbor_transform_dataset(dataset: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Apply Harbor quirks to an entire dataset (for list endpoints)."""
    return {
        "properties": dataset.get("properties", []),
        "reservations": [_to_harbor_reservation(r) for r in dataset.get("reservations", [])],
        "folios": [_to_harbor_folio(f) for f in dataset.get("folios", [])],
    }


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------


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
    # Properties are vendor-neutral, no transform needed
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
    data = [_to_harbor_reservation(r) for r in dataset["reservations"]]

    prop_filter = request.query_params.get("property_id")
    if prop_filter:
        data = [r for r in data if str(_wire_get(r, "property_id", "")) == prop_filter]

    from simulators.common import beacon_serve as _beacon

    contracted = _beacon.maybe_serve_contract(request, data)
    if contracted is not None:
        return contracted

    limit, offset, cursor = get_pagination_params(request)
    # Harbor default is cursor (even without fault, it uses cursor)
    mode = pagination_mode_for_scenario(scenario_id, default="cursor")

    if mode == "cursor":
        sliced, meta = paginate_cursor(data, cursor, limit, scenario_id, count)
    else:
        sliced, meta = paginate_offset(data, limit, offset)
    try:
        from simulators.common.helpers import get_baseline_gross_sums, page_net_sum

        sums = get_baseline_gross_sums()
        meta["gross_sum"] = sums["reservations_gross_sum"]
        # REQ-068 item 1: under gross_to_net the vendor also reports
        # the page's net total next to gross_sum (the honest Approve
        # target). Baseline responses carry no net_sum.
        if scenario_id == "gross_to_net":
            net = page_net_sum(sliced, "total_amount")
            if net is not None:
                meta["net_sum"] = net
    except Exception:
        pass
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
    data = [_to_harbor_folio(f) for f in dataset["folios"]]

    prop_filter = request.query_params.get("property_id")
    if prop_filter:
        data = [r for r in data if str(_wire_get(r, "property_id", "")) == prop_filter]
    res_filter = request.query_params.get("reservation_id")
    if res_filter:
        data = [r for r in data if str(_wire_get(r, "reservation_id", "")) == res_filter]

    from simulators.common import beacon_serve as _beacon

    contracted = _beacon.maybe_serve_contract(request, data)
    if contracted is not None:
        return contracted

    limit, offset, cursor = get_pagination_params(request)
    mode = pagination_mode_for_scenario(scenario_id, default="cursor")
    if mode == "cursor":
        sliced, meta = paginate_cursor(data, cursor, limit, scenario_id, count)
    else:
        sliced, meta = paginate_offset(data, limit, offset)
    try:
        from simulators.common.helpers import get_baseline_gross_sums, page_net_sum

        sums = get_baseline_gross_sums()
        meta["gross_sum"] = sums["folios_gross_sum"]
        if scenario_id == "gross_to_net":
            net = page_net_sum(sliced, "amount")
            if net is not None:
                meta["net_sum"] = net
    except Exception:
        pass
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
        if str(_inner_get(rec, "reservation_id", "")) == reservation_id:
            return {"data": _to_harbor_reservation(rec)}
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Reservation not found"})


@app.get("/reservations/{reservation_id}/folios")
def list_nested_folios(
    reservation_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Nested folios under a reservation (P1.W2.4: Harbor's nested folios).

    This endpoint is intentionally not documented in the Markdown guide — it is
    the undocumented endpoint that discovery must find via probing.

    Cursor pagination; filtered to folios where ``reservation_id`` matches path.
    """
    state = get_state()
    scenario_id = str(state["scenario_id"])
    count = increment_request_count()
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization)
    check_endpoint_removed(scenario_id, "folios")

    dataset = get_dataset_for_current_state()
    # Verify reservation exists (return 404 if not, like a true nested resource)
    reservation_ids = {str(_inner_get(r, "reservation_id", "")) for r in dataset["reservations"]}
    if reservation_id not in reservation_ids:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Reservation not found"})

    data = [f for f in dataset["folios"] if str(_inner_get(f, "reservation_id", "")) == reservation_id]
    data = [_to_harbor_folio(f) for f in data]

    from simulators.common import beacon_serve as _beacon

    contracted = _beacon.maybe_serve_contract(request, data)
    if contracted is not None:
        return contracted

    limit, offset, cursor = get_pagination_params(request)
    mode = pagination_mode_for_scenario(scenario_id, default="cursor")
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
    check_auth(scenario_id, authorization)
    check_endpoint_removed(scenario_id, "folios")

    dataset = get_dataset_for_current_state()
    for rec in dataset["folios"]:
        if str(_inner_get(rec, "charge_id", "")) == charge_id:
            return {"data": _to_harbor_folio(rec)}
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Folio charge not found"})


# Optional alias for docs discovery: serve harbor.md via explicit endpoint
@app.get("/_harbor_docs")
def get_harbor_docs_alias() -> PlainTextResponse:
    """Alias so docs are reachable even if static mount is shadowed."""
    for fname in ("harbor.md", "harborpms.md"):
        cand = DOCS_DIR / fname
        if cand.is_file():
            text = cand.read_text(encoding="utf-8")
            # REQ-083 B6: the faulted round's pagination params ride the
            # served docs — the only channel that names them.
            from simulators.common import beacon_serve as _beacon_docs

            note = _beacon_docs.pagination_docs_note("harbor")
            if note:
                text = text.rstrip("\n") + "\n\n" + note
            return PlainTextResponse(content=text, media_type="text/markdown")
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Harbor docs not found"})
