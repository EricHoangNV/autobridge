"""AtlasPMS simulator public API with fault injection (W2.2 → REQ-019).

AtlasPMS is the documented vendor: a versioned method-style REST surface
(``GET /api/v1.3/getReservations`` etc.) with ``x-api-key`` header auth
(``Authorization: Bearer`` accepted as the OAuth2 alternative), a
``{"success", "data", "count", "total"}`` envelope, and ``pageNumber`` /
``pageSize`` pagination. Money arrives as JSON numbers; date-times are
zone-less ``YYYY-MM-DD HH:MM:SS`` rendered in America/New_York (documented
quirk — see ZONELESS_TZ below).

The records are backed by the same seeded generator as before; this module
only presents them in the documented shape. The fault switchboard keeps
working on it (record mutations happen on the internal records, behaviour
flags switch pagination/auth/transport).

This module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal as _Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo as _ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from simulators.common.devportal import mount_devportal
from simulators.common.faults import (
    is_auth_expired,
    is_auth_revoked,
    is_endpoint_removed,
    pagination_mode_for_scenario,
    should_repeat_cursor,
)
from simulators.common.helpers import (
    check_rate_limit_and_transient,
    decode_cursor,
    encode_cursor,
    get_dataset_for_current_state,
)
from simulators.common.state import get_state, increment_request_count

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

# Zone-less date-times on the wire are property-local times rendered in this
# zone for every property (documented vendor quirk). The engine interprets
# them with timezone_source static America/New_York, so the round-trip is
# exact. Operators assuming UTC misread them by the offset — the real trap.
ZONELESS_TZ = "America/New_York"

API_PREFIX = "/api/v1.3"

app = FastAPI(title="AtlasPMS Simulator", openapi_url=None, docs_url=None, redoc_url=None)

# Serve docs/ directory as static files (e.g. /docs/atlas-openapi.json)
if DOCS_DIR.is_dir():
    app.mount("/docs", StaticFiles(directory=str(DOCS_DIR)), name="docs")

# Human-readable vendor docs site (demo presentation); renders atlas-openapi.yaml at load time.
mount_devportal(app, page="atlas.html", source="atlas-openapi.yaml")


# ---------------------------------------------------------------------------
# JSON numbers without float (money exactness)
# ---------------------------------------------------------------------------


def _dumps_exact(payload: Any) -> str:
    """Serialize payload to JSON with Decimal/float-free money numbers.

    ``decimal.Decimal`` values are emitted as raw JSON numbers (exact,
    never via float). Everything else follows stdlib semantics. Keeps
    money exact from simulator to wire; the engine parses numbers with
    ``parse_float=Decimal``.
    """
    from decimal import Decimal

    def encode(value: Any) -> str:
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            raise ValueError("float must never reach the wire; use Decimal")
        if isinstance(value, str):
            return json.dumps(value)
        if isinstance(value, list):
            return "[" + ",".join(encode(v) for v in value) + "]"
        if isinstance(value, dict):
            parts = [json.dumps(str(k)) + ":" + encode(v) for k, v in value.items()]
            return "{" + ",".join(parts) + "}"
        raise ValueError(f"unserializable {type(value).__name__}")

    return encode(payload)


def _json_response(payload: Any) -> Response:
    return Response(content=_dumps_exact(payload), media_type="application/json")


# ---------------------------------------------------------------------------
# Health / docs
# ---------------------------------------------------------------------------


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
    """Serve AtlasPMS OpenAPI 3.1 docs (P1.W2.3, REQ-019 surface)."""
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


# ---------------------------------------------------------------------------
# Auth (x-api-key header, Bearer accepted as the OAuth2 alternative)
# ---------------------------------------------------------------------------


def _check_atlas_auth(scenario_id: str, x_api_key: str | None, authorization: str | None) -> None:
    """Fault-injected auth failures win; else require x-api-key or Bearer."""
    if is_auth_expired(scenario_id):
        raise HTTPException(status_code=401, detail={"code": "token_expired", "message": "Token has expired"})
    if is_auth_revoked(scenario_id):
        raise HTTPException(status_code=403, detail={"code": "token_revoked", "message": "Token has been revoked"})
    if x_api_key:
        return
    if authorization and authorization.startswith("Bearer ") and authorization.removeprefix("Bearer ").strip():
        return
    raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Missing x-api-key header"})


def _check_removed(scenario_id: str, family: str) -> None:
    if is_endpoint_removed(scenario_id) and family in ("reservations", "folios"):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Endpoint not found"})


# ---------------------------------------------------------------------------
# Presentation: internal records -> documented wire shape
# ---------------------------------------------------------------------------

_NY = _ZoneInfo(ZONELESS_TZ)


def ny_naive(aware_iso: str) -> str:
    """Render an aware UTC ISO instant as zone-less New York local time."""
    dt = datetime.datetime.fromisoformat(aware_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt.astimezone(_NY).strftime("%Y-%m-%d %H:%M:%S")


def guest_id_for(reservation_id: str) -> str:
    return f"G-{reservation_id}"


def adults_children(reservation_id: str) -> tuple[str, str]:
    h = sum(ord(c) for c in reservation_id)
    return str(1 + (h % 2)), str(h % 3)


def _snake_to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in tail)


def _extra_wire_fields(rec: dict[str, Any], consumed: set[str]) -> dict[str, Any]:
    """Surface fault-mutated extra keys in the vendor's camelCase convention.

    The vendor API is camelCase; a renamed or decoy field (e.g. the
    ``invalid_patch_trap`` decoy ``net_amount``) appears under its camelCase
    name so repairs target the real wire shape. Money-like decoys stay JSON
    numbers.
    """
    extra: dict[str, Any] = {}
    for key, value in rec.items():
        if key in consumed:
            continue
        wire_key = _snake_to_camel(key)
        if wire_key in ("netAmount",):
            try:
                extra[wire_key] = _Decimal(str(value))
                continue
            except Exception:
                pass
        extra[wire_key] = value
    return extra


def to_wire_reservation(rec: dict[str, Any]) -> dict[str, Any]:
    """Internal reservation -> getReservations list item (camelCase)."""
    status = rec.get("status")
    wire_status = "canceled" if status == "cancelled" else status
    arrival = str(rec.get("arrival_date", ""))
    updated = rec.get("updated_at")
    date_modified = ny_naive(str(updated)) if updated else ""
    adults, children = adults_children(str(rec.get("reservation_id", "")))
    item: dict[str, Any] = {
        "propertyID": rec.get("property_id"),
        "reservationID": rec.get("reservation_id"),
        "dateCreated": f"{arrival} 12:00:00" if arrival else "",
        "dateModified": date_modified,
        "status": wire_status,
        "guestID": guest_id_for(str(rec.get("reservation_id", ""))),
        "startDate": arrival,
        "endDate": str(rec.get("departure_date", "")),
        "adults": adults,
        "children": children,
        "balance": _Decimal(str(rec.get("total_amount", "0"))),
        "sourceName": "Direct",
    }
    # guestName only when the record actually carries guest_name: a renamed
    # field must vanish from the wire, not show up as null.
    if "guest_name" in rec:
        item["guestName"] = rec.get("guest_name")
    item.update(
        _extra_wire_fields(
            rec,
            {
                "property_id", "reservation_id", "arrival_date", "departure_date",
                "status", "guest_id", "guest_name",
                "total_amount", "currency", "updated_at",
            },
        )
    )
    return item


def to_wire_reservation_detail(rec: dict[str, Any]) -> dict[str, Any]:
    """Internal reservation -> getReservation detail object."""
    item = to_wire_reservation(rec)
    total = _Decimal(str(rec.get("total_amount", "0")))
    guest_name = str(item.get("guestName") or "")
    first, _, last = guest_name.partition(" ")
    return {
        "propertyID": item["propertyID"],
        "guestName": item["guestName"],
        "guestEmail": f"{str(item['reservationID']).lower().replace('_', '-')}@example.com" if guest_name else None,
        "guestList": {
            str(item["guestID"]): {
                "guestID": item["guestID"],
                "guestFirstName": first,
                "guestLastName": last,
                "isMainGuest": True,
            }
        },
        "reservationID": item["reservationID"],
        "dateCreated": item["dateCreated"],
        "dateModified": item["dateModified"],
        "status": item["status"],
        "total": total,
        "balance": total,
        "startDate": item["startDate"],
        "endDate": item["endDate"],
        "sourceName": "Direct",
    }


def to_wire_guest(rec: dict[str, Any]) -> dict[str, Any]:
    """Internal reservation -> getGuest object (primary guest)."""
    guest_name = str(rec.get("guest_name") or "") if "guest_name" in rec else ""
    first, _, last = guest_name.partition(" ")
    rid = str(rec.get("reservation_id", ""))
    return {
        "firstName": first,
        "lastName": last,
        "email": f"{rid.lower().replace('_', '-')}@example.com" if guest_name else None,
    }


def to_wire_property(prop: dict[str, Any]) -> dict[str, Any]:
    return {
        "propertyID": prop.get("property_id"),
        "propertyName": prop.get("name"),
        "propertyTimezone": prop.get("timezone"),
        "propertyCurrency": [{"currencyCode": prop.get("currency")}],
    }


def to_wire_transaction(rec: dict[str, Any]) -> dict[str, Any]:
    """Internal folio -> getTransactions item (classic method-style shape).

    Field names follow the documented v1.3 camelCase conventions; the
    retired classic getTransactions reference page no longer lists them,
    so ``businessDate``/``transactionDateTime``/``transactionID`` are
    documented here as simulator-confirmed rather than reference-confirmed.
    """
    posted = rec.get("posted_at")
    business_date = str(rec.get("business_date", ""))
    return {
        "transactionID": rec.get("charge_id"),
        "propertyID": rec.get("property_id"),
        "reservationID": rec.get("reservation_id"),
        "amount": _Decimal(str(rec.get("amount", "0"))),
        "currency": rec.get("currency"),
        "businessDate": business_date,
        "serviceDate": business_date,
        "transactionDateTime": ny_naive(str(posted)) if posted else "",
        "description": rec.get("description"),
    }


# ---------------------------------------------------------------------------
# Pagination (page baseline; cursor fault mode)
# ---------------------------------------------------------------------------


def _page_params(request: Request, default_size: int) -> tuple[int, int]:
    qp = request.query_params
    try:
        page_number = int(qp.get("pageNumber", "1"))
    except ValueError:
        page_number = 1
    try:
        page_size = int(qp.get("pageSize", str(default_size)))
    except ValueError:
        page_size = default_size
    page_number = max(1, page_number)
    page_size = max(1, min(page_size, 100))
    return page_number, page_size


def _envelope_page(data: list[dict[str, Any]], page_number: int, page_size: int) -> dict[str, Any]:
    total = len(data)
    start = (page_number - 1) * page_size
    sliced = data[start : start + page_size]
    return {"success": True, "data": sliced, "count": len(sliced), "total": total}


def _envelope_cursor(
    data: list[dict[str, Any]],
    cursor: str | None,
    limit: int,
    scenario_id: str,
    request_count: int,
) -> dict[str, Any]:
    """Fault-mode cursor pagination (page_to_cursor / offset_to_cursor / repeat).

    A cursor-paginated listing advertises no total: callers follow
    ``next_cursor`` until it is null.
    """
    offset = decode_cursor(cursor)
    sliced = data[offset : offset + limit]
    next_idx = offset + limit
    if next_idx >= len(data):
        next_cursor = None
    elif should_repeat_cursor(scenario_id) and request_count > 0 and request_count % 2 == 0:
        next_cursor = cursor if cursor else encode_cursor(offset)
    else:
        next_cursor = encode_cursor(next_idx)
    return {"success": True, "data": sliced, "count": len(sliced), "next_cursor": next_cursor}


def _paginate(
    request: Request,
    data: list[dict[str, Any]],
    scenario_id: str,
    request_count: int,
    default_size: int = 100,
) -> dict[str, Any]:
    # REQ-025 beacon: a pagination contract replaces the vendor wire shape
    # entirely (renamed params + renamed envelope). Only the contract's
    # names are read; baseline names fall back to defaults.
    from simulators.common import beacon_serve as _beacon

    contract = _beacon.pagination_contract()
    if contract is not None:
        return _beacon.paginate_by_contract(data, request.query_params, contract, default_size)
    mode = pagination_mode_for_scenario(scenario_id, default="page")
    if mode == "cursor":
        qp = request.query_params
        try:
            limit = int(qp.get("pageSize", qp.get("limit", "50")))
        except ValueError:
            limit = 50
        limit = max(1, min(limit, 100))
        return _envelope_cursor(data, qp.get("cursor"), limit, scenario_id, request_count)
    page_number, page_size = _page_params(request, default_size)
    return _envelope_page(data, page_number, page_size)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _request_prologue(scenario_family: str | None = None) -> tuple[str, int]:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    count = increment_request_count()
    check_rate_limit_and_transient(scenario_id, count)
    if scenario_family is not None:
        _check_removed(scenario_id, scenario_family)
    return scenario_id, count


@app.get(API_PREFIX + "/getHotels")
def get_hotels(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Response:
    scenario_id, _ = _request_prologue()
    _check_atlas_auth(scenario_id, x_api_key, authorization)
    dataset = get_dataset_for_current_state()
    props = [to_wire_property(p) for p in dataset["properties"]]
    qp = request.query_params
    ids = qp.get("propertyIDs")
    if ids:
        wanted = {s.strip() for s in ids.split(",")}
        props = [p for p in props if str(p.get("propertyID")) in wanted]
    name = qp.get("propertyName")
    if name:
        props = [p for p in props if name.lower() in str(p.get("propertyName", "")).lower()]
    page_number, page_size = _page_params(request, 20)
    return _json_response(_envelope_page(props, page_number, page_size))


@app.get(API_PREFIX + "/getReservations")
def get_reservations(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Response:
    scenario_id, count = _request_prologue("reservations")
    _check_atlas_auth(scenario_id, x_api_key, authorization)
    dataset = get_dataset_for_current_state()
    recs = dataset["reservations"]
    qp = request.query_params
    prop_filter = qp.get("propertyID")
    if prop_filter:
        recs = [r for r in recs if str(r.get("property_id")) == prop_filter]
    wire = [to_wire_reservation(r) for r in recs]
    status_filter = qp.get("status")
    if status_filter:
        wire = [w for w in wire if str(w.get("status")) == status_filter]
    for lo, hi, key in (
        ("modifiedFrom", "modifiedTo", "dateModified"),
        ("checkInFrom", "checkInTo", "startDate"),
        ("checkOutFrom", "checkOutTo", "endDate"),
        ("resultsFrom", "resultsTo", "dateCreated"),
    ):
        lo_v, hi_v = qp.get(lo), qp.get(hi)
        if lo_v:
            wire = [w for w in wire if str(w.get(key, "")) >= lo_v]
        if hi_v:
            wire = [w for w in wire if str(w.get(key, "")) <= hi_v]
    return _json_response(_paginate(request, wire, scenario_id, count))


@app.get(API_PREFIX + "/getReservation")
def get_reservation(
    reservationID: str | None = Query(default=None),
    propertyID: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Response:
    scenario_id, _ = _request_prologue("reservations")
    _check_atlas_auth(scenario_id, x_api_key, authorization)
    if not reservationID:
        raise HTTPException(status_code=422, detail={"code": "missing_param", "message": "reservationID is required"})
    dataset = get_dataset_for_current_state()
    for rec in dataset["reservations"]:
        if str(rec.get("reservation_id")) == reservationID and (propertyID is None or str(rec.get("property_id")) == propertyID):
            return _json_response({"success": True, "data": to_wire_reservation_detail(rec)})
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Reservation not found"})


@app.get(API_PREFIX + "/getGuest")
def get_guest(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Response:
    scenario_id, _ = _request_prologue("reservations")
    _check_atlas_auth(scenario_id, x_api_key, authorization)
    qp = request.query_params
    reservation_id = qp.get("reservationID")
    guest_id = qp.get("guestID")
    if not reservation_id and not guest_id:
        raise HTTPException(status_code=422, detail={"code": "missing_param", "message": "reservationID or guestID is required"})
    dataset = get_dataset_for_current_state()
    for rec in dataset["reservations"]:
        rid = str(rec.get("reservation_id"))
        if (reservation_id is not None and rid == reservation_id) or (
            guest_id is not None and guest_id_for(rid) == guest_id
        ):
            return _json_response({"success": True, "data": to_wire_guest(rec)})
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Guest not found"})


@app.get(API_PREFIX + "/getTransactions")
def get_transactions(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Response:
    scenario_id, count = _request_prologue("folios")
    _check_atlas_auth(scenario_id, x_api_key, authorization)
    dataset = get_dataset_for_current_state()
    recs = dataset["folios"]
    qp = request.query_params
    prop_filter = qp.get("propertyID")
    if prop_filter:
        recs = [r for r in recs if str(r.get("property_id")) == prop_filter]
    res_filter = qp.get("reservationID")
    if res_filter:
        recs = [r for r in recs if str(r.get("reservation_id")) == res_filter]
    wire = [to_wire_transaction(r) for r in recs]
    return _json_response(_paginate(request, wire, scenario_id, count))
