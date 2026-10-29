"""Serve ablation faults in-process through real simulator wire code.

REQ-024 owner check on f32b4b4b: engine arms must observe the fault
(``execute_sync`` → detector → classifier → hypothesis engine → ladder)
instead of receiving case-supplied drift labels. This module serves each
dev HTTP scenario's fault over :class:`httpx.MockTransport` using the
SAME wire functions the live simulators use, so fakes and live serving
cannot drift apart:

* Atlas: ``to_wire_reservation`` / ``to_wire_transaction`` plus the
  Cloudbeds page/cursor envelopes.
* Harbor: ``_to_harbor_reservation`` / ``_to_harbor_folio`` plus
  ``paginate_cursor`` and gross sums.
* Meridian: ``to_wire_*`` + ``_paginate_by_cursor`` (mirrors the e2e
  meridian fake, which serves through the same functions).

Beacon instances (REQ-025) are served through the vendors' REAL FastAPI
apps in-process (:func:`beacon_sync_pair`): the same
:func:`beacon_serve.set_beacon` the live ``/admin/beacon`` endpoint
calls, the same app code, the same seed-pinned dataset — deterministic
from the public ``(eval_seed_hex, index)`` with or without a live stack.

Only ``(scenario_id, seed)`` — or the public beacon descriptor — cross
this boundary: never a drift class, delta, hypothesis or yield count.
Datasets come from ``simulators.common.generator.generate_dataset(seed)``
mutated by ``apply_dataset_mutations`` (the same builders the simulators
use), and transport/auth faults follow the ``is_*`` predicates in
``simulators.common.faults`` with a per-client request counter.

Trust boundary: this module lives in top-level ``bench/`` (the harness),
not in ``src/autobridge`` — it may drive simulator serving code the way
the tests do. It never imports ``verifier`` (answer keys stay in their
package; grading goes through the verifier's HTTP hidden mode).
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

_FAKE_BASE_URL = "https://bench-fault.invalid"

#: Fixture env placeholders the served specs resolve. Applied with
#: save/restore around each served sync (never leaked process-globally:
#: unit tests assert on unset env elsewhere in the suite).
_FAKE_ENV_DEFAULTS: dict[str, str] = {
    "ATLAS_BASE_URL": _FAKE_BASE_URL,
    "HARBOR_BASE_URL": _FAKE_BASE_URL,
    "LEGACYSTAY_BASE_URL": _FAKE_BASE_URL,
    "MERIDIAN_BASE_URL": _FAKE_BASE_URL,
    "ATLAS_API_KEY": "bench-test-token",
    "HARBOR_API_KEY": "bench-test-token",
    "LEGACYSTAY_API_KEY": "bench-test-token",
    "MERIDIAN_CLIENT_TOKEN": "meridian-client-token",
    "MERIDIAN_ACCESS_TOKEN": "meridian-access-token",
}


def _ensure_fake_env() -> None:
    """Placeholder env the specs resolve (never override a real deployment).

    Reference specs carry ``${ATLAS_BASE_URL}`` / ``${HARBOR_BASE_URL}``
    placeholders that :func:`resolve_env_placeholders` refuses to leave
    empty; the in-process fakes below never open a socket (MockTransport),
    so any URL works. Auth placeholders likewise resolve to fixture
    values — the fakes ignore auth except for the fault-injected
    scenarios (401/429/503), which the handlers raise themselves.
    """
    for key, value in _FAKE_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)


@contextmanager
def fake_env() -> Any:
    """Apply :data:`_FAKE_ENV_DEFAULTS` with exact save/restore.

    Test-isolation guard: ``setdefault`` must not leak into the pytest
    process (other unit tests assert on unset env). Restores every key
    to its prior state — set or unset — on exit.
    """
    saved = {key: os.environ.get(key) for key in _FAKE_ENV_DEFAULTS}
    _ensure_fake_env()
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def fault_client_for(scenario_id: str, *, seed: int, connector_id: str) -> Any:
    """Return an :class:`HttpClient` serving *scenario_id*'s fault.

    The client serves the faulted shape for the scenario's vendor
    (``connector_id``: ``atlaspms`` | ``harborpms`` | ``meridianpms``).
    Raises :class:`KeyError` for unknown scenarios and :class:`ValueError`
    for non-HTTP (FIAS) faults — the bench arms are HTTP only.
    """
    from simulators.common.faults import KNOWN_SCENARIO_IDS, transport_for_scenario

    if scenario_id not in KNOWN_SCENARIO_IDS:
        raise KeyError(f"no fault serving for unknown scenario {scenario_id!r}")
    if transport_for_scenario(scenario_id) != "http":
        raise ValueError(f"scenario {scenario_id!r} is not an HTTP bench fault (skipped)")
    # No env touch here: placeholders resolve at request time inside fake_env().
    if connector_id == "meridianpms":
        return _meridian_fault_client(scenario_id, seed=seed)
    if connector_id == "harborpms":
        return _harbor_fault_client(scenario_id, seed=seed)
    if connector_id == "atlaspms":
        return _atlas_fault_client(scenario_id, seed=seed)
    raise KeyError(f"no fault serving for connector {connector_id!r}")


def _mutated_dataset(scenario_id: str, seed: int) -> dict[str, list[dict[str, Any]]]:
    """Deterministic dataset for (scenario, seed), fault applied."""
    from simulators.common.faults import apply_dataset_mutations
    from simulators.common.generator import generate_dataset

    return apply_dataset_mutations(generate_dataset(seed=seed), scenario_id)


def _transport_gate(scenario_id: str, count: int) -> Any | None:
    """Fault-injected 429/503/401, mirroring the simulator prologue.

    Returns an ``httpx.Response`` to short-circuit the request, or None
    to serve normally. Uses the same predicates the live apps use.
    """
    import httpx as _httpx

    from simulators.common.faults import (
        is_auth_expired,
        is_rate_limited,
        is_transient_error,
    )

    if is_rate_limited(scenario_id, count):
        return _httpx.Response(
            429,
            json={"code": "rate_limit", "message": "Too many requests"},
            headers={"Retry-After": "1"},
        )
    if is_transient_error(scenario_id, count):
        return _httpx.Response(503, json={"code": "transient_error", "message": "Service temporarily unavailable"})
    if is_auth_expired(scenario_id):
        return _httpx.Response(401, json={"code": "token_expired", "message": "Token has expired"})
    return None


def _mock_client(handler: Any) -> Any:
    """Wrap a ``(request) -> Response`` handler in the runtime HttpClient."""
    import httpx as _httpx

    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import RetryPolicy

    transport = _httpx.MockTransport(handler)

    def factory(**kw: Any) -> _httpx.Client:
        return _httpx.Client(transport=transport, timeout=kw.get("timeout", 5))

    return HttpClient(
        _FAKE_BASE_URL,
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=5),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def _atlas_fault_client(scenario_id: str, *, seed: int) -> Any:
    """Atlas fault serving: page envelope baseline, cursor when mutated."""
    import httpx as _httpx

    from simulators.atlaspms.app import (
        _dumps_exact,
        to_wire_reservation,
        to_wire_transaction,
    )
    from simulators.common.faults import (
        is_pagination_mutated,
        should_repeat_cursor,
    )
    from simulators.common.helpers import decode_cursor, encode_cursor

    dataset = _mutated_dataset(scenario_id, seed)
    cursor_shape = bool(is_pagination_mutated(scenario_id))
    state = {"n": 0}

    def _page_envelope(records: list[dict[str, Any]], qp: dict[str, str]) -> bytes:
        # Page-style (pageNumber/pageSize) and offset-style (offset/limit)
        # listings share the Cloudbeds page envelope; honor both.
        try:
            page_size = max(1, min(100, int(qp.get("pageSize", qp.get("limit", "50")))))
        except ValueError:
            page_size = 50
        if "offset" in qp:
            try:
                start = max(0, int(qp.get("offset", "0")))
            except ValueError:
                start = 0
        else:
            try:
                page_number = max(1, int(qp.get("pageNumber", "1")))
            except ValueError:
                page_number = 1
            start = (page_number - 1) * page_size
        total = len(records)
        sliced = records[start : start + page_size]
        return _dumps_exact({"success": True, "data": sliced, "count": len(sliced), "total": total}).encode()

    def _cursor_envelope(
        records: list[dict[str, Any]], cursor: str | None, limit: int, repeat: bool
    ) -> bytes:
        offset = decode_cursor(cursor)
        sliced = records[offset : offset + limit]
        next_idx = offset + limit
        if next_idx >= len(records):
            next_cursor = None
        elif repeat:
            next_cursor = cursor if cursor else encode_cursor(offset)
        else:
            next_cursor = encode_cursor(next_idx)
        return _dumps_exact(
            {"success": True, "data": sliced, "count": len(sliced), "next_cursor": next_cursor}
        ).encode()

    def handler(request: _httpx.Request) -> _httpx.Response:
        state["n"] += 1
        gated = _transport_gate(scenario_id, state["n"])
        if gated is not None:
            return gated
        qp = dict(request.url.params)
        path = request.url.path
        if path == "/api/v1.3/getReservations":
            wire = [to_wire_reservation(r) for r in dataset.get("reservations", [])]
        elif path == "/api/v1.3/getTransactions":
            wire = [to_wire_transaction(r) for r in dataset.get("folios", [])]
        else:
            return _httpx.Response(404, json={"msg": "not found"})
        if cursor_shape:
            try:
                limit = max(1, min(100, int(qp.get("pageSize", qp.get("limit", "50")))))
            except ValueError:
                limit = 50
            repeat = bool(should_repeat_cursor(scenario_id)) and state["n"] > 1 and state["n"] % 2 == 0
            body = _cursor_envelope(wire, qp.get("cursor"), limit, repeat)
        else:
            body = _page_envelope(wire, qp)
        return _httpx.Response(200, content=body, headers={"Content-Type": "application/json"})

    return _mock_client(handler)


def _harbor_fault_client(scenario_id: str, *, seed: int) -> Any:
    """Harbor fault serving: messy shape + cursor pagination + gross sums."""
    import httpx as _httpx

    from simulators.common.helpers import get_baseline_gross_sums, paginate_cursor
    from simulators.harborpms.app import (
        _to_harbor_folio,
        _to_harbor_reservation,
    )

    dataset = _mutated_dataset(scenario_id, seed)
    baseline = _mutated_dataset("baseline", seed)
    sums = {
        "reservations_gross_sum": str(sum(_amt(r.get("total_amount")) for r in baseline.get("reservations", []))),
        "folios_gross_sum": str(sum(_amt(f.get("amount")) for f in baseline.get("folios", []))),
    }
    try:
        live_sums = get_baseline_gross_sums()
        if isinstance(live_sums, dict) and live_sums.get("reservations_gross_sum"):
            sums = {k: str(v) for k, v in live_sums.items() if k in sums}
    except Exception:
        pass
    state = {"n": 0}

    def handler(request: _httpx.Request) -> _httpx.Response:
        state["n"] += 1
        gated = _transport_gate(scenario_id, state["n"])
        if gated is not None:
            return gated
        qp = dict(request.url.params)
        path = request.url.path
        try:
            limit = max(1, min(100, int(qp.get("limit", "10"))))
        except ValueError:
            limit = 10
        cursor = qp.get("cursor")
        if path == "/reservations":
            data = [_to_harbor_reservation(r) for r in dataset.get("reservations", [])]
            prop_filter = qp.get("property_id")
            if prop_filter:
                data = [r for r in data if str(r.get("property_id")) == prop_filter]
            sliced, meta = paginate_cursor(data, cursor, limit, scenario_id, state["n"])
            meta = dict(meta)
            meta["gross_sum"] = sums["reservations_gross_sum"]
            return _httpx.Response(200, json={"data": sliced, **meta})
        if path == "/folios":
            data = [_to_harbor_folio(f) for f in dataset.get("folios", [])]
            prop_filter = qp.get("property_id")
            if prop_filter:
                data = [f for f in data if str(f.get("property_id")) == prop_filter]
            res_filter = qp.get("reservation_id")
            if res_filter:
                data = [f for f in data if str(f.get("reservation_id")) == res_filter]
            sliced, meta = paginate_cursor(data, cursor, limit, scenario_id, state["n"])
            meta = dict(meta)
            meta["gross_sum"] = sums["folios_gross_sum"]
            return _httpx.Response(200, json={"data": sliced, **meta})
        return _httpx.Response(404, json={"msg": "not found"})

    return _mock_client(handler)


def _amt(value: Any) -> Any:
    from decimal import Decimal as _Decimal

    try:
        return _Decimal(str(value))
    except Exception:
        return _Decimal("0")


# ---------------------------------------------------------------------------
# Beacon serving: real vendor apps in-process, isolated simulator state
# ---------------------------------------------------------------------------


_BEACON_VENDOR_APPS: dict[str, str] = {
    "atlas": "simulators.atlaspms.app",
    "harbor": "simulators.harborpms.app",
    "meridian": "simulators.meridianpms.app",
    "legacystay": "simulators.legacystay.app",
}


@contextmanager
def isolated_sim_state() -> Iterator[None]:
    """Isolate in-process serving from any live stack sharing this host.

    Simulator state syncs through a JSON file (``/tmp/sim_state.json`` by
    default) that a running local stack also reads and writes. Repoint it
    at a private temp file and save/restore the in-memory state around
    the serving, so bench syncs can neither observe nor disturb live
    services (and vice versa).
    """
    import tempfile as _tempfile

    from simulators.common import state as _sim_state

    saved_state = dict(_sim_state._state)
    saved_file = _sim_state._STATE_FILE
    with _tempfile.TemporaryDirectory(prefix="bench-sim-state-") as tmpdir:
        _sim_state._STATE_FILE = type(saved_file)(os.path.join(tmpdir, "sim_state.json"))
        _sim_state._state.clear()
        _sim_state._state.update({"scenario_id": "baseline", "seed": 0, "request_count": 0, "beacon": None})
        try:
            yield
        finally:
            _sim_state._state.clear()
            _sim_state._state.update(saved_state)
            _sim_state._STATE_FILE = saved_file


def _testclient_http_client(app: Any, spec: dict[str, Any]) -> Any:
    """HttpClient serving through a real ASGI app (one TestClient per request).

    Mirrors :func:`_mock_client` but drives the vendor app, with the
    spec's own auth + retry policy (same as ``sync_candidate``). Each
    request gets a fresh ``TestClient`` (closed by ``HttpClient.request``'s
    context manager), so no portal thread leaks across requests.
    """
    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import load_spec_from_dict

    spec_obj = load_spec_from_dict(spec)
    # Keep the spec's base path (e.g. Meridian's /api/connector/v1): the
    # vendor app routes include it, and placeholders resolve via the
    # fixture env above. Only the host is fake (MockTransport/TestClient
    # never open a socket).
    import urllib.parse as _urlparse

    from autobridge.runtime.http import resolve_env_placeholders as _resolve

    base_path = _urlparse.urlsplit(_resolve(str(spec_obj.source.base_url))).path.rstrip("/")
    fake_base = _FAKE_BASE_URL + base_path

    def factory(**_kw: Any) -> Any:
        from starlette.testclient import TestClient as _TestClient

        return _TestClient(app, base_url=fake_base)

    return HttpClient(
        fake_base,
        auth=spec_obj.source.auth,
        retry=spec_obj.source.rate_limit.retry,
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def beacon_sync_pair(
    *, vendor: str, eval_seed_hex: str, index: int, spec: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(faulted_summary, baseline_summary) for a beacon instance, in-process.

    Activates the instance with the same :func:`beacon_serve.set_beacon`
    the live ``/admin/beacon`` endpoint calls (fail closed on bad seed or
    index), syncs the pre-drift ``spec`` against the vendor's real app,
    then clears the beacon slot and syncs the same pinned dataset without
    the instance mutation. Both summaries come from
    :func:`execute_sync` — the arm derives detector input from their
    difference exactly like the ablation path.
    """
    import importlib as _importlib

    from autobridge.engine.sync_run import execute_sync
    from autobridge.spec.models import load_spec_from_dict
    from simulators.common import beacon_serve as _beacon

    try:
        module_name = _BEACON_VENDOR_APPS[vendor]
    except KeyError:
        raise ValueError(f"no in-process beacon serving for vendor {vendor!r}") from None
    load_spec_from_dict(spec)  # fail fast on an invalid pre-drift spec
    # fake_env save/restores: placeholder env must not leak process-globally.
    with isolated_sim_state(), fake_env():
        app = _importlib.import_module(module_name).app
        client = _testclient_http_client(app, spec)
        try:
            _beacon.set_beacon(eval_seed_hex, index)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"invalid beacon instance vendor={vendor} index={index}: {exc}") from exc
        faulted_summary = execute_sync(spec, client)
        # Clear the beacon slot for the true baseline (the dataset seed
        # stays pinned by set_beacon, so both syncs share one dataset).
        from simulators.common import state as _sim_state

        _sim_state.set_beacon_state(None, None)
        baseline_summary = execute_sync(spec, client)
    return faulted_summary, baseline_summary


def _meridian_fault_client(scenario_id: str, *, seed: int) -> Any:
    """Meridian fault serving (mirrors the e2e meridian fake, same wire fns)."""
    import httpx as _httpx

    from simulators.common.generator import generate_dataset
    from simulators.meridianpms.app import (
        _dumps_exact,
        _paginate_by_cursor,
        account_id_for,
        to_wire_customer,
        to_wire_enterprise,
        to_wire_order_item,
        to_wire_reservation,
    )

    dataset = generate_dataset(seed=seed)
    state = {"n": 0}

    def handler(request: _httpx.Request) -> _httpx.Response:
        state["n"] += 1
        gated = _transport_gate(scenario_id, state["n"])
        if gated is not None:
            return gated
        try:
            body = json.loads(request.content.decode() or "{}")
        except Exception:
            body = {}
        path = request.url.path
        lim = body.get("Limitation") or {}
        try:
            count = max(1, min(1000, int(lim.get("Count", 100))))
        except (TypeError, ValueError):
            count = 100
        cursor = lim.get("Cursor")
        leaf = "NextCursor" if scenario_id == "meridian_cursor_renamed" else "Cursor"
        if "reservations" in path:
            wires = [to_wire_reservation(r, scenario_id) for r in dataset.get("reservations", [])]
            page, out = _paginate_by_cursor(wires, count, cursor)
            return _httpx.Response(
                200,
                content=_dumps_exact({"Reservations": page, leaf: out}).encode(),
                headers={"Content-Type": "application/json"},
            )
        if "customers" in path:
            wires = []
            for r in dataset.get("reservations", []):
                account = account_id_for(str(r.get("reservation_id")))
                guest = r.get("guest_name")
                wires.append(
                    to_wire_customer(account, str(guest) if guest is not None else None, "2026-10-26T10:00:00Z")
                )
            page, out = _paginate_by_cursor(wires, count, cursor)
            return _httpx.Response(
                200,
                content=_dumps_exact({"Customers": page, leaf: out}).encode(),
                headers={"Content-Type": "application/json"},
            )
        if "orderItems" in path:
            wires = [to_wire_order_item(f) for f in dataset.get("folios", [])]
            page, out = _paginate_by_cursor(wires, count, cursor)
            return _httpx.Response(
                200,
                content=_dumps_exact({"OrderItems": page, leaf: out}).encode(),
                headers={"Content-Type": "application/json"},
            )
        if "configuration" in path:
            payload = {"Enterprises": [to_wire_enterprise(p) for p in dataset.get("properties", [])]}
            return _httpx.Response(
                200, content=_dumps_exact(payload).encode(), headers={"Content-Type": "application/json"}
            )
        return _httpx.Response(404, json={"msg": "not found"})

    return _mock_client(handler)
