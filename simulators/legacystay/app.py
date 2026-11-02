"""LegacyStay simulator public API with fault injection (P2.S1.1).

LegacyStay is the heldout-only unseen-generalization vendor: a legacy plain-JSON
API with offset pagination, strict Bearer auth, plain-English statuses,
property-local dates, an ambiguous ``date`` convenience field, nullable chaos
(guest_name / description often null), and aggressive baseline rate limiting
(roughly every eighth request is 429 with ``Retry-After: 1``).

Quirks implemented here:
- Offset pagination (``limit`` / ``offset`` / ``next_offset``), unlike Harbor.
- Strict auth: ``Authorization: Bearer <token>`` required (``require_bearer``).
- Plain-English status vocabulary (no opaque codes).
- Ambiguous ``date`` field: duplicates ``arrival_date`` (reservations) and
  ``business_date`` (folios); docs call it "the date" without saying which.
- Nullable chaos: deterministic extra nulling of ``guest_name`` (~1/4) and
  ``description`` (~1/4) on top of the seeded generator nulls.
- Baseline throttle: every 8th request returns 429 (``LegacyStay-Throttle``),
  independent of the fault-injected ``rate_limit_429`` catalog scenario.
- Markdown docs served from ``simulators/docs/legacystay.md``; readyz checks
  docs presence.

This module must never import autobridge or verifier (trust boundary).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
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

# Baseline throttle cadence: every Nth request is 429 (LegacyStay is always
# throttled; this is its normal contract, not a fault-injected scenario).
THROTTLE_EVERY_N = 8

app = FastAPI(title="LegacyStay Simulator", openapi_url=None, docs_url=None, redoc_url=None)

# Serve docs/ directory as static files (e.g. /docs/legacystay.md)
if DOCS_DIR.is_dir():
    app.mount("/docs", StaticFiles(directory=str(DOCS_DIR)), name="docs")

# Human-readable vendor docs site (demo presentation); renders legacystay.md at load time.
mount_devportal(app, page="legacystay.html", source="legacystay.md")


@app.get("/healthz")
def get_healthz() -> dict[str, str]:
    return {"status": "ok"}


def _legacystay_readyz_docs_ok() -> bool:
    return any((DOCS_DIR / n).is_file() for n in ("legacystay.md", "legacystay.txt"))


@app.get("/readyz")
def get_readyz() -> dict[str, object]:
    state = get_state()
    docs_ok = _legacystay_readyz_docs_ok()
    checks: dict[str, object] = {
        "default": "ok",
        "scenario": "ok",
        "docs": "ok" if docs_ok else "missing",
    }
    status = "ok" if docs_ok else "degraded"
    return {"status": status, "checks": checks, "scenario": state["scenario_id"]}


# ---------------------------------------------------------------------------
# LegacyStay record transforms
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# LegacyStay record transforms
# ---------------------------------------------------------------------------


def _split_nesting(rec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Unwrap beacon-nested internal records (REQ-083 D17).

    Mirrors the harbor/atlas rule: project the inner record (quirks
    intact), then re-apply the parents to the wire record.
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


def _to_legacystay_reservation(rec: dict[str, Any]) -> dict[str, Any]:
    """Map a canonical reservation record to LegacyStay's messy shape."""
    # REQ-083 D17: nest the projected wire record, not the internal one.
    _parents: list[str] = []
    rec, _parents = _split_nesting(rec if isinstance(rec, dict) else {})
    out = dict(rec)
    # Ambiguous `date` field: duplicates arrival_date value; docs call it "the date"
    if "arrival_date" in out:
        out["date"] = out["arrival_date"]
    # REQ-083 A1: the nullable-chaos quirk is gone. Nulling names the
    # baseline has made even the unfaulted pre-drift sync fail the
    # records judge (``field_missing``), so no arm could ever pass a
    # LegacyStay ``repair`` instance; the vendor serves what it has.
    if _parents:
        return _wrap_wire(out, _parents)
    return out


def _to_legacystay_folio(rec: dict[str, Any]) -> dict[str, Any]:
    """Map a canonical folio record to LegacyStay's messy shape."""
    _parents: list[str] = []
    rec, _parents = _split_nesting(rec if isinstance(rec, dict) else {})
    out = dict(rec)
    if "business_date" in out:
        out["date"] = out["business_date"]
    if _parents:
        return _wrap_wire(out, _parents)
    return out


def check_legacystay_throttle(request_count: int) -> None:
    """Baseline throttle: every Nth request is 429 with Retry-After.

    Independent of the fault-injected ``rate_limit_429`` catalog scenario
    (different cadence on purpose: the generic retry rule must not be overfit
    to the catalog's every-3rd pattern).
    """
    if request_count > 0 and request_count % THROTTLE_EVERY_N == 0:
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limit", "message": "Too many requests"},
            headers={"Retry-After": "1"},
        )


def _gross_sums_meta() -> dict[str, str]:
    """Baseline gross sums for the W6.2 money invariant (independent evidence)."""
    try:
        from simulators.common.helpers import get_baseline_gross_sums

        sums = get_baseline_gross_sums()
        return {"gross_sum": sums["reservations_gross_sum"]}
    except Exception:
        return {}


def _gross_sums_meta_folios() -> dict[str, str]:
    try:
        from simulators.common.helpers import get_baseline_gross_sums

        sums = get_baseline_gross_sums()
        return {"gross_sum": sums["folios_gross_sum"]}
    except Exception:
        return {}


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
    check_legacystay_throttle(count)
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
    check_legacystay_throttle(count)
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization, require_bearer=True)
    check_endpoint_removed(scenario_id, "reservations")

    dataset = get_dataset_for_current_state()
    data = [_to_legacystay_reservation(r) for r in dataset["reservations"]]

    prop_filter = request.query_params.get("property_id")
    if prop_filter:
        data = [r for r in data if str(_wire_get(r, "property_id", "")) == prop_filter]

    from simulators.common import beacon_serve as _beacon

    contracted = _beacon.maybe_serve_contract(request, data)
    if contracted is not None:
        return contracted

    limit, offset, cursor = get_pagination_params(request)
    # LegacyStay default is offset (even without fault, it uses offset)
    mode = pagination_mode_for_scenario(scenario_id, default="offset")

    if mode == "cursor":
        sliced, meta = paginate_cursor(data, cursor, limit, scenario_id, count)
    else:
        sliced, meta = paginate_offset(data, limit, offset)
    meta.update(_gross_sums_meta())
    # REQ-068 item 1: same honest net total as Harbor under gross_to_net.
    if scenario_id == "gross_to_net":
        try:
            from simulators.common.helpers import page_net_sum as _page_net_sum

            net = _page_net_sum(sliced, "total_amount")
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
    check_legacystay_throttle(count)
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization, require_bearer=True)
    check_endpoint_removed(scenario_id, "folios")

    dataset = get_dataset_for_current_state()
    data = [_to_legacystay_folio(f) for f in dataset["folios"]]

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
    mode = pagination_mode_for_scenario(scenario_id, default="offset")
    if mode == "cursor":
        sliced, meta = paginate_cursor(data, cursor, limit, scenario_id, count)
    else:
        sliced, meta = paginate_offset(data, limit, offset)
    meta.update(_gross_sums_meta_folios())
    if scenario_id == "gross_to_net":
        try:
            from simulators.common.helpers import page_net_sum as _page_net_sum

            net = _page_net_sum(sliced, "amount")
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
    check_legacystay_throttle(count)
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization, require_bearer=True)
    check_endpoint_removed(scenario_id, "reservations")

    dataset = get_dataset_for_current_state()
    for rec in dataset["reservations"]:
        if str(_inner_get(rec, "reservation_id", "")) == reservation_id:
            return {"data": _to_legacystay_reservation(rec)}
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Reservation not found"})


@app.get("/folios/{charge_id}")
def get_folio(
    charge_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = get_state()
    scenario_id = str(state["scenario_id"])
    count = increment_request_count()
    check_legacystay_throttle(count)
    check_rate_limit_and_transient(scenario_id, count)
    check_auth(scenario_id, authorization, require_bearer=True)
    check_endpoint_removed(scenario_id, "folios")

    dataset = get_dataset_for_current_state()
    for rec in dataset["folios"]:
        if str(_inner_get(rec, "charge_id", "")) == charge_id:
            return {"data": _to_legacystay_folio(rec)}
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Folio charge not found"})


# Optional alias for docs discovery: serve legacystay.md via explicit endpoint
@app.get("/_legacystay_docs")
def get_legacystay_docs_alias() -> PlainTextResponse:
    """Alias so docs are reachable even if static mount is shadowed."""
    for fname in ("legacystay.md", "legacystay.txt"):
        cand = DOCS_DIR / fname
        if cand.is_file():
            text = cand.read_text(encoding="utf-8")
            # REQ-083 B6: the faulted round's pagination params ride the
            # served docs — the only channel that names them.
            from simulators.common import beacon_serve as _beacon_docs

            note = _beacon_docs.pagination_docs_note("legacystay")
            if note:
                text = text.rstrip("\n") + "\n\n" + note
            return PlainTextResponse(content=text, media_type="text/markdown")
    return JSONResponse(status_code=404, content={"detail": {"code": "not_found", "message": "LegacyStay docs not found"}})
