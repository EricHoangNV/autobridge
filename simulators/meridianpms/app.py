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
    from simulators.common import beacon_serve as _beacon

    contract = _beacon.pagination_contract()
    size_param, cursor_param = "Count", "Cursor"
    if contract is not None and str(contract.get("strategy")) == "body_cursor":
        # REQ-025 beacon: the vendor renames the page-size leaf and the
        # cursor leaf. Only the contract names are read; an unfixed client
        # still sending Count/Cursor gets defaults.
        size_param = str(contract.get("page_size_param", "Count"))
        cursor_param = str(contract.get("cursor_leaf", "Cursor"))
    raw = body.get("Limitation", {size_param: 100})
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail={"code": "invalid_limitation", "message": "Limitation must be an object"})
    count = raw.get(size_param, 100)
    cursor = raw.get(cursor_param)
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 1000:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_limitation", "message": f"Limitation.{size_param} must be an integer between 1 and 1000"},
        )
    if cursor is not None and not isinstance(cursor, str):
        raise HTTPException(
            status_code=400, detail={"code": "invalid_limitation", "message": f"Limitation.{cursor_param} must be a string or null"}
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
    from simulators.common import beacon_serve as _beacon

    contract = _beacon.pagination_contract()
    if contract is not None and str(contract.get("strategy")) == "body_cursor":
        return str(contract.get("cursor_leaf", "Cursor"))
    return "NextCursor" if scenario_id == "meridian_cursor_renamed" else "Cursor"


def _paginate_by_cursor(items: list[dict[str, Any]], count: int, cursor: str | None) -> tuple[list[dict[str, Any]], str | None]:
    """Slice ``items`` (Id ascending) after ``cursor``; Cursor is the last id.

    The returned cursor is ``None`` only when the page carries no data, so a
    client stops on a null cursor or a short page — the documented contract.
    """
    ordered = sorted(items, key=lambda r: str(_wire_value(r, "Id", "")))
    start = 0
    if cursor is not None:
        ids = [str(_wire_value(r, "Id", "")) for r in ordered]
        try:
            start = ids.index(cursor) + 1
        except ValueError:
            start = 0  # unknown cursor restarts from the beginning
    page = ordered[start : start + count]
    out_cursor = str(_wire_value(page[-1], "Id", "")) if page else None
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


def _parse_iso_date(value: Any) -> datetime.date | None:
    """Strict ISO date parse (None when missing or non-ISO).

    REQ-078 item 8: beacon date faults serve renamed, nested or non-ISO
    dates; the serializer must serve them, never 500. Non-ISO values are
    reported as missing below and ride the wire verbatim as extras, so the
    drift is observable.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _parse_iso_dt(value: Any) -> datetime.datetime | None:
    """Strict ISO datetime parse (None when missing or non-ISO)."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.UTC)
    return moment


#: Internal record keys the reservation serializer consumes; anything else
#: (a beacon rename target, a nesting parent wrapper, a raw non-ISO date)
#: rides the wire verbatim so a mutated shape is served, never a 500.
_RESERVATION_CONSUMED = frozenset(
    {
        "reservation_id", "property_id", "arrival_date", "departure_date",
        "status", "guest_name", "total_amount", "currency", "updated_at",
    }
)

#: Internal record keys the order-item serializer consumes.
_FOLIO_CONSUMED = frozenset(
    {
        "charge_id", "reservation_id", "property_id", "amount",
        "currency", "description", "posted_at", "business_date",
    }
)


def _wire_extras(rec: dict[str, Any], consumed: frozenset[str]) -> dict[str, Any]:
    """Surface fault-mutated keys the fixed wire shape does not consume."""
    return {k: v for k, v in rec.items() if k not in consumed}


def _split_nesting(rec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Unwrap beacon-nested internal records (REQ-081 item 5).

    A nesting mutation wraps the internal record as ``{parent: {...}}``
    (possibly several levels). The vendor projects the inner record and
    re-applies the same parents to the wire record — as a real vendor
    would — so a JSONPath patch (``$.parent.Id``) can repair it. Returns
    ``(inner, parents)``; a flat record returns ``(rec, [])``.
    """
    inner = rec if isinstance(rec, dict) else {}
    parents: list[str] = []
    for _ in range(5):
        if not isinstance(inner, dict):
            break
        # Flat when it carries any consumed key.
        if any(
            k in inner
            for k in (
                "reservation_id",
                "property_id",
                "charge_id",
                "arrival_date",
                "departure_date",
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


def _wire_value(wire: Any, key: str, default: Any = None) -> Any:
    """Leaf value from a possibly nesting-wrapped wire record (REQ-084).

    The nesting fault wraps the projected wire as ``{parent: {...}}``; a
    filter or cursor that reads ``w["ServiceId"]`` would KeyError on the
    wrapper and a sync would 500 instead of serving the nested shape a
    path patch can repair. Descend through single-key wrappers until the
    key is found or the record is flat.
    """
    seen = 0
    node = wire
    while isinstance(node, dict) and seen < 6:
        if key in node:
            return node[key]
        if len(node) == 1:
            _k, _v = next(iter(node.items()))
            if isinstance(_v, dict) and isinstance(_k, str) and _k:
                node = _v
                seen += 1
                continue
        return default
    return default


def service_id_for(property_id: str) -> str:
    """Service id for a property (REQ-083 B5).

    The bare canonical property id — like every other vendor's wire, the
    served service id must equal the canonical ``property_id`` the specs
    map it to, so an unfaulted sync agrees with the oracle exactly. The
    old ``SVC-`` namespace carried no information (it prefixes the
    property id verbatim) and made every canonical comparison fail.
    """
    return str(property_id)


def account_id_for(reservation_id: str) -> str:
    return f"ACC-{reservation_id}"


def to_wire_reservation(rec: dict[str, Any], scenario_id: str = "baseline") -> dict[str, Any]:
    """Internal reservation -> getAll reservation (no name, no money).

    Under ``meridian_state_renamed`` the vendor renames the ``State`` leaf
    to ``Status`` (REQ-020 item 6: a renamed field repaired through the
    ladder).

    REQ-078 item 8: beacon mutations (renamed/nested/non-ISO dates) never
    crash the serializer. Unparseable dates null the derived UTC fields
    and ride the wire verbatim as extras, so the drift is observable.
    """
    # REQ-081 item 5: nesting applies to the projected wire record, not
    # the internal record — unwrap, project, then re-wrap the wire so a
    # path patch ($.parent.Id) can repair it.
    _parents: list[str] = []
    try:
        rec, _parents = _split_nesting(rec if isinstance(rec, dict) else {})
    except Exception:
        _parents = []
    arrival = _parse_iso_date(rec.get("arrival_date"))
    departure = _parse_iso_date(rec.get("departure_date"))
    updated = _parse_iso_dt(rec.get("updated_at"))
    state = _STATE_MAP.get(str(rec.get("status")), str(rec.get("status")))
    actual_start = _utc_z(datetime.datetime(arrival.year, arrival.month, arrival.day, 16, 0)) if arrival is not None and state in ("Started", "Processed") else None
    actual_end = _utc_z(datetime.datetime(departure.year, departure.month, departure.day, 10, 0)) if departure is not None and state == "Processed" else None
    wire = {
        "Id": rec.get("reservation_id"),
        "ServiceId": service_id_for(str(rec.get("property_id"))),
        "AccountId": account_id_for(str(rec.get("reservation_id"))),
        "BookerId": account_id_for(str(rec.get("reservation_id"))),
        "Number": _number_for(str(rec.get("reservation_id"))),
        "State": state,
        "Origin": "Direct",
        "CreatedUtc": _utc_z(datetime.datetime(arrival.year, arrival.month, arrival.day, 10, 0)) if arrival is not None else None,
        "UpdatedUtc": _utc_z(updated) if updated is not None else None,
        "ScheduledStartUtc": _utc_z(datetime.datetime(arrival.year, arrival.month, arrival.day, 15, 0)) if arrival is not None else None,
        "ScheduledEndUtc": _utc_z(datetime.datetime(departure.year, departure.month, departure.day, 11, 0)) if departure is not None else None,
        "ActualStartUtc": actual_start,
        "ActualEndUtc": actual_end,
        "RateId": f"RATE-{rec.get('property_id')}-STD",
        "AssignedResourceId": f"ROOM-{rec.get('property_id')}-{_number_for(str(rec.get('reservation_id')))[:3]}",
    }
    if scenario_id == "meridian_state_renamed" and "State" in wire:
        wire["Status"] = wire.pop("State")
    # REQ-080 item 1: a renamed field vanishes from the wire. Derived keys
    # whose source record field is gone are dropped, never served as null
    # placeholders while the new key (in extras) holds the values.
    # Present-but-unparseable values still null the derived field and ride
    # the wire verbatim as extras (REQ-078 item 8 — the dates family).
    if "status" not in rec:
        wire.pop("State", None)
        wire.pop("Status", None)
    if "arrival_date" not in rec:
        for _drop in ("CreatedUtc", "ScheduledStartUtc", "ActualStartUtc"):
            wire.pop(_drop, None)
    if "departure_date" not in rec:
        for _drop in ("ScheduledEndUtc", "ActualEndUtc"):
            wire.pop(_drop, None)
    if "updated_at" not in rec:
        wire.pop("UpdatedUtc", None)
    extras = _wire_extras(rec, _RESERVATION_CONSUMED)
    # A present-but-non-ISO date was consumed above and nulled: surface the
    # raw value so the format change is observable on the wire.
    for key in ("arrival_date", "departure_date", "updated_at"):
        raw = rec.get(key)
        if isinstance(raw, str) and key not in extras:
            if key == "updated_at":
                parsed: Any = _parse_iso_dt(raw)
            else:
                parsed = _parse_iso_date(raw)
            if parsed is None:
                extras[key] = raw
    wire.update(extras)
    if _parents:
        return _wrap_wire(wire, _parents)
    return wire


def to_wire_customer(account_id: str, guest_name: str | None, updated_utc: str | None) -> dict[str, Any]:
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
    """Internal folio charge -> getAll order item (money lives here).

    REQ-078 item 8: beacon mutations (renamed/nested dates, renamed money
    or description keys) never crash the serializer. Unparseable instants
    null ``PostedUtc`` and ride the wire verbatim as extras; amounts stay
    Decimal end to end.
    """
    # REQ-081 item 5: nesting applies to the projected wire record.
    _parents: list[str] = []
    try:
        rec, _parents = _split_nesting(rec if isinstance(rec, dict) else {})
    except Exception:
        _parents = []
    try:
        gross = _Decimal(str(rec.get("amount", "0")))
    except Exception:
        gross = _Decimal("0")
    net, tax = _split_gross(gross)
    currency = rec.get("currency") or "USD"
    currency = str(currency)
    description = rec.get("description")
    posted = _parse_iso_dt(rec.get("posted_at"))
    wire: dict[str, Any] = {
        "Id": rec.get("charge_id"),
        "AccountId": account_id_for(str(rec.get("reservation_id"))),
        "ServiceId": service_id_for(str(rec.get("property_id"))),
    }
    # REQ-080 item 1: a renamed field vanishes from the wire (never a
    # backfilled default while the new key holds the values). Baseline
    # order is preserved: Name, UnitCount, UnitAmount, PostedUtc.
    # REQ-083 A2: no backfilled default either — a null description serves
    # null (the oracle holds null), so the pre-drift spec over the
    # unfaulted simulator agrees with the oracle exactly.
    if "description" in rec:
        wire["Name"] = description
    wire["UnitCount"] = 1
    if "amount" in rec:
        wire["UnitAmount"] = {
            "Currency": currency,
            "NetValue": net,
            "GrossValue": gross,
            "TaxValues": [{"Code": "VAT10", "Value": tax}],
        }
    if "posted_at" in rec:
        wire["PostedUtc"] = _utc_z(posted) if posted is not None else None
    extras = _wire_extras(rec, _FOLIO_CONSUMED)
    raw_posted = rec.get("posted_at")
    if isinstance(raw_posted, str) and "posted_at" not in extras and posted is None:
        extras["posted_at"] = raw_posted
    wire.update(extras)
    if _parents:
        return _wrap_wire(wire, _parents)
    return wire


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
        wires = [w for w in wires if str(_wire_value(w, "ServiceId", "")).removeprefix("SVC-") in wanted]
    if service_ids is not None:
        wires = [w for w in wires if str(_wire_value(w, "ServiceId", "")) in set(service_ids)]
    if reservation_ids is not None:
        wires = [w for w in wires if str(_wire_value(w, "Id", "")) in set(reservation_ids)]
    if states is not None:
        wires = [w for w in wires if str(_wire_value(w, "State", _wire_value(w, "Status", ""))) in set(states)]
    if interval is not None:
        start, end = interval
        wires = [w for w in wires if start <= str(_wire_value(w, "UpdatedUtc", "")) <= end]
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
        # REQ-084 item 1: the nesting fault wraps the internal record —
        # unwrap before reading reservation_id / guest_name / updated_at,
        # as to_wire_reservation and to_wire_order_item already do. Without
        # this the endpoint serves Id "ACC-None" and drops the names, and no
        # spec patch can repoint a join to data that is not served.
        # (_split_nesting is total over any input: no fallback needed.)
        inner, _parents = _split_nesting(r if isinstance(r, dict) else {})
        account = account_id_for(str(inner.get("reservation_id")))
        updated = _parse_iso_dt(inner.get("updated_at"))
        wire = to_wire_customer(account, inner.get("guest_name"), _utc_z(updated) if updated is not None else None)  # type: ignore[arg-type]
        # REQ-080 item 1: a renamed field vanishes from the wire — the
        # customer row's derived names/instant drop when the source record
        # no longer carries the field. Baseline nulls (field present but
        # None) keep today's null keys.
        if "guest_name" not in inner:
            for _drop in ("FirstName", "LastName", "Email"):
                wire.pop(_drop, None)
        if "updated_at" not in inner:
            wire.pop("UpdatedUtc", None)
        wires.append(wire)
    if customer_ids is not None:
        wires = [w for w in wires if str(_wire_value(w, "Id", "")) in set(customer_ids)]
    if interval is not None:
        start, end = interval
        wires = [w for w in wires if start <= str(_wire_value(w, "UpdatedUtc", "")) <= end]
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
        wires = [w for w in wires if str(_wire_value(w, "ServiceId", "")).removeprefix("SVC-") in wanted]
    if account_ids is not None:
        wires = [w for w in wires if str(_wire_value(w, "AccountId", "")) in set(account_ids)]
    if order_item_ids is not None:
        wires = [w for w in wires if str(_wire_value(w, "Id", "")) in set(order_item_ids)]
    if interval is not None:
        start, end = interval
        wires = [w for w in wires if start <= str(_wire_value(w, "UpdatedUtc", "")) <= end]
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
        text = cand.read_text(encoding="utf-8")
        # REQ-083 B6: the faulted round's pagination params ride the
        # served docs (as YAML comments, so the document still parses) —
        # the only channel that names them.
        from simulators.common import beacon_serve as _beacon_docs

        note = _beacon_docs.pagination_docs_note("meridian")
        if note:
            commented = "\n".join(f"# {line}" if line.strip() else "#" for line in note.splitlines())
            text = text.rstrip("\n") + "\n\n" + commented + "\n"
        return PlainTextResponse(content=text, media_type="text/yaml")
    return JSONResponse(status_code=404, content={"detail": {"code": "not_found", "message": "Meridian docs not found"}})


@app.post("/api/connector/v1/{rest:path}")
def wrong_version_guard(rest: str, body: dict[str, Any] | None = None) -> Response:
    """Any versioned path that is not the supported one → 404, documented."""
    _ = (rest, body)
    raise HTTPException(
        status_code=404,
        detail={"code": "not_found", "message": "Unknown operation or API version; see the Changelog for supported versions"},
    )
