"""MeridianPMS simulator public API with fault injection (REQ-020).

MeridianPMS is the "modern but very different" vendor: every operation is
``POST`` with a JSON body (``POST /api/connector/v1/<resource>/getAll/2023-06-06``),
auth travels in the body (``ClientToken`` / ``AccessToken`` / ``Client``),
and paging uses a body ``Limitation`` (``{"Count": 1..1000, "Cursor": "<id or null>"}``).
The response ``Cursor`` is the id of the last item returned and is ``null``
only when no data comes back; clients stop on a null cursor or a short page.

Reservations carry no guest name and no money: the name comes from customers
(``AccountId``) and money from order items (net / gross / tax with currency),
and every date is a UTC instant — property-local business dates need the
enterprise time zone from the configuration call. Those joins and conversions
are the point of this vendor.

Records are backed by the same seeded generator as the other vendors; this
module only presents them in the documented shape. The fault switchboard keeps
working on the internal records (mutations) and on behaviour flags
(rate limit / transient / auth / endpoint-removed). Pagination-shape faults
from the shared catalog do not apply to the Limitation contract and are
ignored here (never a crash).

This module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal as _Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from simulators.common.devportal import mount_devportal
from simulators.common.faults import is_auth_expired, is_auth_revoked
from simulators.common.helpers import (
    check_endpoint_removed,
    check_rate_limit_and_transient,
    get_dataset_for_current_state,
)
from simulators.common.state import get_state, increment_request_count

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

VERSION = "2023-06-06"
API_PREFIX = "/api/connector/v1"

# Demo credentials: documented in meridian-openapi.yaml as the values the
# docs tell integrators to paste. Real deployments issue per-enterprise
# tokens; the simulator accepts exactly these two.
VALID_CLIENT_TOKEN = "meridian-client-token"
VALID_ACCESS_TOKEN = "meridian-access-token"

MAX_IDS_PER_FILTER = 1000
MAX_UPDATED_INTERVAL_DAYS = 92  # "max 3 months" per the documented contract

app = FastAPI(title="MeridianPMS Simulator", openapi_url=None, docs_url=None, redoc_url=None)

# Serve docs/ directory as static files (e.g. /docs/meridian-openapi.yaml)
if DOCS_DIR.is_dir():
    app.mount("/docs", StaticFiles(directory=str(DOCS_DIR)), name="docs")

# Human-readable vendor docs site (demo presentation); renders
# meridian-openapi.yaml at load time.
mount_devportal(app, page="meridian.html", source="meridian-openapi.yaml")


# ---------------------------------------------------------------------------
# JSON numbers without float (money exactness)
# ---------------------------------------------------------------------------


def _dumps_exact(payload: Any) -> str:
    """Serialize payload to JSON with Decimal/float-free money numbers."""
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
# Health / readyz
# ---------------------------------------------------------------------------


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    state = get_state()
    docs_ok = (DOCS_DIR / "meridian-openapi.yaml").is_file()
    checks: dict[str, object] = {
        "default": "ok",
        "scenario": "ok",
        "docs": "ok" if docs_ok else "missing",
    }
    status = "ok" if docs_ok else "degraded"
    return {"status": status, "checks": checks, "scenario": state["scenario_id"]}


# ---------------------------------------------------------------------------
# Body auth (ClientToken / AccessToken in the JSON body, never a header)
# ---------------------------------------------------------------------------


def _check_meridian_auth(scenario_id: str, body: dict[str, Any]) -> None:
    """Fault-injected auth failures win; else require valid body tokens.

    Missing tokens → 401; wrong tokens → 403, per the documented contract.
    """
    if is_auth_expired(scenario_id):
        raise HTTPException(status_code=401, detail={"code": "token_expired", "message": "Token has expired"})
    if is_auth_revoked(scenario_id):
        raise HTTPException(status_code=403, detail={"code": "token_revoked", "message": "Token has been revoked"})
    client_token = body.get("ClientToken")
    access_token = body.get("AccessToken")
    if not client_token or not access_token:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "Missing ClientToken or AccessToken in the request body"},
        )
    if client_token != VALID_CLIENT_TOKEN or access_token != VALID_ACCESS_TOKEN:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Invalid ClientToken or AccessToken"},
        )


# ---------------------------------------------------------------------------
# Limitation cursor pagination + shared filters
# ---------------------------------------------------------------------------


def _limitation(body: dict[str, Any]) -> tuple[int, str | None]:
    """Parse ``Limitation`` into (count, cursor); 400 on contract breach."""
    raw = body.get("Limitation", {"Count": 100})
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail={"code": "invalid_limitation", "message": "Limitation must be an object"})
    count = raw.get("Count", 100)
    cursor = raw.get("Cursor")
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 1000:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_limitation", "message": "Limitation.Count must be an integer between 1 and 1000"},
        )
    if cursor is not None and not isinstance(cursor, str):
        raise HTTPException(
            status_code=400, detail={"code": "invalid_limitation", "message": "Limitation.Cursor must be a string or null"}
        )
    return count, cursor


def _id_filter(body: dict[str, Any], key: str) -> list[str] | None:
    raw = body.get(key)
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
        raise HTTPException(status_code=400, detail={"code": "invalid_filter", "message": f"{key} must be a list of strings"})
    if len(raw) > MAX_IDS_PER_FILTER:
        raise HTTPException(
            status_code=400, detail={"code": "too_many_ids", "message": f"{key} accepts at most 1000 ids"}
        )
    return raw


def _updated_interval(body: dict[str, Any]) -> tuple[str, str] | None:
    raw = body.get("UpdatedUtc")
    if raw is None:
        return None
    if not isinstance(raw, dict) or "StartUtc" not in raw or "EndUtc" not in raw:
        raise HTTPException(
            status_code=400, detail={"code": "invalid_filter", "message": "UpdatedUtc needs StartUtc and EndUtc"}
        )
    try:
        start = datetime.datetime.fromisoformat(str(raw["StartUtc"]).replace("Z", "+00:00"))
        end = datetime.datetime.fromisoformat(str(raw["EndUtc"]).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400, detail={"code": "invalid_filter", "message": "UpdatedUtc bounds must be UTC instants"}
        ) from None
    if end < start:
        raise HTTPException(
            status_code=400, detail={"code": "invalid_filter", "message": "UpdatedUtc EndUtc is before StartUtc"}
        )
    if (end - start).days > MAX_UPDATED_INTERVAL_DAYS:
        raise HTTPException(
            status_code=400, detail={"code": "interval_too_long", "message": "UpdatedUtc interval is longer than 3 months"}
        )
    return str(raw["StartUtc"]), str(raw["EndUtc"])


def _cursor_leaf(scenario_id: str) -> str:
    """Response cursor leaf name (Limitation cursor contract, REQ-020 item 6).

    Under ``meridian_cursor_renamed`` the vendor renames the response
    ``Cursor`` leaf to ``NextCursor``; specs reading ``$.Cursor`` then stop
    after the first page.
    """
    return "NextCursor" if scenario_id == "meridian_cursor_renamed" else "Cursor"


def _paginate_by_cursor(items: list[dict[str, Any]], count: int, cursor: str | None) -> tuple[list[dict[str, Any]], str | None]:
    """Slice ``items`` (Id ascending) after ``cursor``; Cursor is the last id.

    The returned cursor is ``None`` only when the page carries no data, so a
    client stops on a null cursor or a short page — the documented contract.
    """
    ordered = sorted(items, key=lambda r: str(r.get("Id", "")))
    start = 0
    if cursor is not None:
        ids = [str(r.get("Id", "")) for r in ordered]
        try:
            start = ids.index(cursor) + 1
        except ValueError:
            start = 0  # unknown cursor restarts from the beginning
    page = ordered[start : start + count]
    out_cursor = str(page[-1].get("Id", "")) if page else None
    return page, out_cursor


# ---------------------------------------------------------------------------
# Presentation: internal records -> documented PascalCase wire shape
# ---------------------------------------------------------------------------

_STATE_MAP = {
    "confirmed": "Confirmed",
    "checked_in": "Started",
    "checked_out": "Processed",
    "cancelled": "Canceled",
    "no_show": "Optional",
}


def _utc_z(moment: datetime.datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.UTC)
    return moment.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number_for(reservation_id: str) -> str:
    return str(100000 + (sum(ord(c) for c in reservation_id) % 900000))


def service_id_for(property_id: str) -> str:
    return f"SVC-{property_id}"


def account_id_for(reservation_id: str) -> str:
    return f"ACC-{reservation_id}"


def to_wire_reservation(rec: dict[str, Any], scenario_id: str = "baseline") -> dict[str, Any]:
    """Internal reservation -> getAll reservation (no name, no money).

    Under ``meridian_state_renamed`` the vendor renames the ``State`` leaf
    to ``Status`` (REQ-020 item 6: a renamed field repaired through the
    ladder).
    """
    arrival = datetime.date.fromisoformat(str(rec.get("arrival_date")))
    departure = datetime.date.fromisoformat(str(rec.get("departure_date")))
    updated = datetime.datetime.fromisoformat(str(rec.get("updated_at")))
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=datetime.UTC)
    state = _STATE_MAP.get(str(rec.get("status")), str(rec.get("status")))
    actual_start = _utc_z(datetime.datetime(arrival.year, arrival.month, arrival.day, 16, 0)) if state in ("Started", "Processed") else None
    actual_end = _utc_z(datetime.datetime(departure.year, departure.month, departure.day, 10, 0)) if state == "Processed" else None
    wire = {
        "Id": rec.get("reservation_id"),
        "ServiceId": service_id_for(str(rec.get("property_id"))),
        "AccountId": account_id_for(str(rec.get("reservation_id"))),
        "BookerId": account_id_for(str(rec.get("reservation_id"))),
        "Number": _number_for(str(rec.get("reservation_id"))),
        "State": state,
        "Origin": "Direct",
        "CreatedUtc": _utc_z(datetime.datetime(arrival.year, arrival.month, arrival.day, 10, 0)),
        "UpdatedUtc": _utc_z(updated),
        "ScheduledStartUtc": _utc_z(datetime.datetime(arrival.year, arrival.month, arrival.day, 15, 0)),
        "ScheduledEndUtc": _utc_z(datetime.datetime(departure.year, departure.month, departure.day, 11, 0)),
        "ActualStartUtc": actual_start,
        "ActualEndUtc": actual_end,
        "RateId": f"RATE-{rec.get('property_id')}-STD",
        "AssignedResourceId": f"ROOM-{rec.get('property_id')}-{_number_for(str(rec.get('reservation_id')))[:3]}",
    }
    if scenario_id == "meridian_state_renamed" and "State" in wire:
        wire["Status"] = wire.pop("State")
    return wire


def to_wire_customer(account_id: str, guest_name: str | None, updated_utc: str) -> dict[str, Any]:
    """Account holder -> getAll customer."""
    first: str | None
    last: str | None
    if guest_name:
        head, _, tail = guest_name.partition(" ")
        first, last = head or None, tail or None
    else:
        first, last = None, None
    email = None
    if first and last:
        email = f"{first}.{last}@example.com".lower().replace(" ", "")
    return {
        "Id": account_id,
        "FirstName": first,
        "LastName": last,
        "Email": email,
        "Phone": None,
        "UpdatedUtc": updated_utc,
    }


def _split_gross(gross: _Decimal) -> tuple[_Decimal, _Decimal]:
    """Split a gross amount into (net, tax) at 10% VAT, exact Decimals."""
    net = (gross / _Decimal("1.1")).quantize(_Decimal("0.01"))
    return net, gross - net


def to_wire_order_item(rec: dict[str, Any]) -> dict[str, Any]:
    """Internal folio charge -> getAll order item (money lives here)."""
    gross = _Decimal(str(rec.get("amount", "0")))
    net, tax = _split_gross(gross)
    currency = str(rec.get("currency", "USD"))
    description = rec.get("description")
    return {
        "Id": rec.get("charge_id"),
        "AccountId": account_id_for(str(rec.get("reservation_id"))),
        "ServiceId": service_id_for(str(rec.get("property_id"))),
        "Name": description if description else "Room charge",
        "UnitCount": 1,
        "UnitAmount": {
            "Currency": currency,
            "NetValue": net,
            "GrossValue": gross,
            "TaxValues": [{"Code": "VAT10", "Value": tax}],
        },
        "PostedUtc": _utc_z(datetime.datetime.fromisoformat(str(rec.get("posted_at")))),
    }


def to_wire_enterprise(prop: dict[str, Any]) -> dict[str, Any]:
    """Internal property -> configuration enterprise (time zone + currency)."""
    return {
        "Id": prop.get("property_id"),
        "Name": prop.get("name"),
        "TimeZone": prop.get("timezone", "UTC"),
        "CurrencyCode": prop.get("currency", "USD"),
    }


# ---------------------------------------------------------------------------
# POST operations (the whole API is POST with a JSON body)
# ---------------------------------------------------------------------------


def _prelude(scenario_id: str, body: dict[str, Any], family: str) -> None:
    """Shared per-request gate: auth, endpoint-removed, rate limits."""
    count = increment_request_count()
    check_rate_limit_and_transient(scenario_id, count)
    _check_meridian_auth(scenario_id, body)
    check_endpoint_removed(scenario_id, family)


@app.post(f"{API_PREFIX}/reservations/getAll/{VERSION}")
def reservations_get_all(body: dict[str, Any]) -> Response:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    _prelude(scenario_id, body, "reservations")
    count, cursor = _limitation(body)
    enterprise_ids = _id_filter(body, "EnterpriseIds")
    service_ids = _id_filter(body, "ServiceIds")
    reservation_ids = _id_filter(body, "ReservationIds")
    states = body.get("States")
    if states is not None and (not isinstance(states, list) or not all(isinstance(v, str) for v in states)):
        raise HTTPException(status_code=400, detail={"code": "invalid_filter", "message": "States must be a list of strings"})
    interval = _updated_interval(body)

    dataset = get_dataset_for_current_state()
    wires = [to_wire_reservation(r, scenario_id) for r in dataset.get("reservations", [])]
    if enterprise_ids is not None:
        wanted = set(enterprise_ids)
        wires = [w for w in wires if str(w["ServiceId"]).removeprefix("SVC-") in wanted]
    if service_ids is not None:
        wires = [w for w in wires if str(w["ServiceId"]) in set(service_ids)]
    if reservation_ids is not None:
        wires = [w for w in wires if str(w["Id"]) in set(reservation_ids)]
    if states is not None:
        wires = [w for w in wires if str(w.get("State", w.get("Status"))) in set(states)]
    if interval is not None:
        start, end = interval
        wires = [w for w in wires if start <= str(w["UpdatedUtc"]) <= end]
    page, out_cursor = _paginate_by_cursor(wires, count, cursor)
    return _json_response({"Reservations": page, _cursor_leaf(scenario_id): out_cursor})


@app.post(f"{API_PREFIX}/customers/getAll/{VERSION}")
def customers_get_all(body: dict[str, Any]) -> Response:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    _prelude(scenario_id, body, "customers")
    count, cursor = _limitation(body)
    customer_ids = _id_filter(body, "CustomerIds")
    interval = _updated_interval(body)

    dataset = get_dataset_for_current_state()
    wires = []
    for r in dataset.get("reservations", []):
        account = account_id_for(str(r.get("reservation_id")))
        updated = _utc_z(datetime.datetime.fromisoformat(str(r.get("updated_at"))))
        # A renamed-away guest_name stays absent: the customer row keeps its
        # Id (accounts exist) but carries null names.
        wires.append(to_wire_customer(account, r.get("guest_name"), updated))  # type: ignore[arg-type]
    if customer_ids is not None:
        wires = [w for w in wires if str(w["Id"]) in set(customer_ids)]
    if interval is not None:
        start, end = interval
        wires = [w for w in wires if start <= str(w["UpdatedUtc"]) <= end]
    page, out_cursor = _paginate_by_cursor(wires, count, cursor)
    return _json_response({"Customers": page, _cursor_leaf(scenario_id): out_cursor})


@app.post(f"{API_PREFIX}/orderItems/getAll/{VERSION}")
def order_items_get_all(body: dict[str, Any]) -> Response:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    _prelude(scenario_id, body, "folios")
    count, cursor = _limitation(body)
    enterprise_ids = _id_filter(body, "EnterpriseIds")
    account_ids = _id_filter(body, "AccountIds")
    order_item_ids = _id_filter(body, "OrderItemIds")
    interval = _updated_interval(body)

    dataset = get_dataset_for_current_state()
    wires = [to_wire_order_item(f) for f in dataset.get("folios", [])]
    if enterprise_ids is not None:
        wanted = set(enterprise_ids)
        wires = [w for w in wires if str(w["ServiceId"]).removeprefix("SVC-") in wanted]
    if account_ids is not None:
        wires = [w for w in wires if str(w["AccountId"]) in set(account_ids)]
    if order_item_ids is not None:
        wires = [w for w in wires if str(w["Id"]) in set(order_item_ids)]
    if interval is not None:
        start, end = interval
        wires = [w for w in wires if start <= str(w["PostedUtc"]) <= end]
    page, out_cursor = _paginate_by_cursor(wires, count, cursor)
    return _json_response({"OrderItems": page, _cursor_leaf(scenario_id): out_cursor})


@app.post(f"{API_PREFIX}/configuration/get/{VERSION}")
def configuration_get(body: dict[str, Any]) -> Response:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    _prelude(scenario_id, body, "configuration")
    enterprise_ids = _id_filter(body, "EnterpriseIds")

    dataset = get_dataset_for_current_state()
    enterprises = [to_wire_enterprise(p) for p in dataset.get("properties", [])]
    if enterprise_ids is not None:
        enterprises = [e for e in enterprises if str(e["Id"]) in set(enterprise_ids)]
    return _json_response({"Enterprises": enterprises})


# ---------------------------------------------------------------------------
# Docs alias + wrong-version guard
# ---------------------------------------------------------------------------


@app.get("/_meridian_docs")
def get_meridian_docs_alias() -> PlainTextResponse:
    """Alias so docs are reachable even if the static mount is shadowed."""
    cand = DOCS_DIR / "meridian-openapi.yaml"
    if cand.is_file():
        return PlainTextResponse(content=cand.read_text(encoding="utf-8"), media_type="text/yaml")
    return JSONResponse(status_code=404, content={"detail": {"code": "not_found", "message": "Meridian docs not found"}})


@app.post("/api/connector/v1/{rest:path}")
def wrong_version_guard(rest: str, body: dict[str, Any] | None = None) -> Response:
    """Any versioned path that is not the supported one → 404, documented."""
    _ = (rest, body)
    raise HTTPException(
        status_code=404,
        detail={"code": "not_found", "message": "Unknown operation or API version; see the Changelog for supported versions"},
    )
