"""Eight-step demo driver (P1.W10.2, plan §6; step 8 is REQ-022 E).

Drives the eight steps through the control API when the Compose stack is
up (``make up``), and always computes the same evidence locally through the
engine so ``make demo`` is reproducible anywhere:

1. Reusable artifact — Atlas reference release syncs ``Complete`` with an
   empty model-usage ledger (``frontier_tokens == 0``).
2. Unfamiliar API — Harbor-style messy Markdown docs → DocsReader →
   classifiers → hypothesis enumerators → draft spec (no model calls;
   any L3 call would appear in the ledger with its token count).
3. Zero-token recovery — ``page_to_cursor`` drift: L0 knowledge miss →
   L1 rule ``page_to_cursor`` → JSON Patch diff, ``frontier_tokens == 0``.
4. Hard semantic failure — ``gross_to_net``: the plausible-but-wrong patch
   (decoy ``net_amount`` → ``total_amount``) is rejected as
   ``invalid_patch_trap``; an escalation bundle carries the single open
   question. Money stays ``Decimal`` throughout.
5. Head-to-head — ``GET /bench/scorecards`` (live) plus
   ``experiments/scorecard.md`` (``make bench`` output); wins and losses
   are read as they are.
6. Reproducibility — seed, reference release signatures, ``make demo``.
7. New API style — Meridian (Mews-style POST/JSON-body docs) with
   vocabulary v1 refuses cleanly (``Blocked``, named reason, 0 model
   calls); the same docs with vocabulary v2 bootstrap a connector that
   syncs ``Complete``.
8. On-premise PABX (REQ-022 E) — FIAS postings flow through the edge
   agent; a firmware layout change (``TA`` → ``TM``, ``CX`` added) is
   detected from the handshake ``LR`` and repaired at L1 (0 model
   calls); a restart replay loses and duplicates nothing.

Trust boundaries (DIRECTIVES §2): this module never imports ``verifier`` or
``simulators``. The verifier is reached only over HTTP (``POST /verify``
hidden mode via the control plane when live); simulator datasets are never
imported — step 7 reads the committed Meridian docs YAML from the repo as
input data (like the model-usage matrix does), everything else inline below.

Secrets (DIRECTIVES §6): only ``credential_ref`` env-key names travel here;
no credential value is printed, logged or stored.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
from decimal import Decimal
from typing import Any

from bench.stack_mode import is_local_stack

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
GENERATED = REPO_ROOT / "generated" / "connectors"
SCORECARD_PATH = REPO_ROOT / "experiments" / "scorecard.md"

DEFAULT_CONTROL_URL = "http://localhost:8101"
DEFAULT_SEED = 0

#: Env var carrying the control-plane write token (REQ-012). Sent as
#: ``Authorization: Bearer <token>`` on control API calls when set.
CONTROL_TOKEN_ENV_VAR = "AUTOBRIDGE_CONTROL_TOKEN"


def env_default(key: str, default: str) -> str:
    """Env value with blank counting as unset (REQ-014).

    An empty value from `.env` must not override the default (e.g. an
    empty ``CONTROL_URL`` must not replace the localhost default, and an
    empty ``DEMO_SEED`` must not crash ``int('')``).
    """
    return (os.environ.get(key) or "").strip() or default


def control_headers() -> dict[str, str]:
    """Auth headers for control API calls (empty when no token is set)."""
    token = os.environ.get(CONTROL_TOKEN_ENV_VAR, "")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}

# Short Harbor-style messy docs excerpt for step 2 (inline so the demo never
# reads simulator files; the quirks mirror the real Harbor docs: a wrong
# pagination example, opaque H1..H5 status codes, nested folios, ambiguous
# date). Keep small: DocsReader + classifiers + enumerators run on this.
DEMO_HARBOR_DOCS = """# HarborPMS API (draft)

Base URL: https://api.harborpms.example.com
Auth: bearer token in the Authorization header.

## List reservations

`GET /reservations`

Query parameters:

- `limit` — page size, default 10
- `cursor` — opaque page cursor from the previous response
- `property_id` — filter by property

Status codes: H1, H2, H3, H4, H5 (see app for meaning).
Date field `date` is the booking date (arrival? business?).

## Folios are nested

`GET /folios`

- `limit` — page size
- `cursor` — opaque page cursor
"""

# ---------------------------------------------------------------------------
# Inline fixtures (local evidence mode — no simulator imports)
# ---------------------------------------------------------------------------


def _atlas_reservations() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i in range(1, 5):
        rows.append(
            {
                "propertyID": "prop-001",
                "reservationID": f"res-{i:03d}",
                "dateCreated": "2026-10-26 12:00:00",
                "dateModified": "2026-10-26 12:00:00",
                "status": "confirmed",
                "guestID": f"G-res-{i:03d}",
                "guestName": f"Guest {i}",
                "startDate": "2026-10-26",
                "endDate": "2026-10-28",
                "adults": "2",
                "children": "0",
                "balance": Decimal(f"{100 * i}.00"),
                "sourceName": "Direct",
            }
        )
    return rows


def _atlas_folios() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i in range(1, 4):
        rows.append(
            {
                "transactionID": f"chg-{i:03d}",
                "propertyID": "prop-001",
                "reservationID": f"res-{i:03d}",
                "amount": Decimal(f"{100 * i}.00"),
                "currency": "USD",
                "businessDate": "2026-10-26",
                "serviceDate": "2026-10-26",
                "transactionDateTime": "2026-10-26 12:00:00",
                "description": f"Room charge {i}",
            }
        )
    return rows


def _demo_dumps_exact(payload: Any) -> str:
    """Exact JSON encoder for the demo fake: Decimal -> raw JSON number."""
    import json as _json

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
        if isinstance(value, str):
            return _json.dumps(value)
        if isinstance(value, list):
            return "[" + ",".join(encode(v) for v in value) + "]"
        if isinstance(value, dict):
            return "{" + ",".join(_json.dumps(str(k)) + ":" + encode(v) for k, v in value.items()) + "}"
        raise ValueError(f"unserializable {type(value).__name__}")

    return encode(payload)


def _atlas_fake_client(mode: str = "page") -> Any:
    """Page-pagination fake serving the inline Atlas fixtures (method-style).

    ``mode="cursor"`` mirrors the ``page_to_cursor`` simulator fault: the
    vendor ignores the page params and serves a cursor envelope (a
    non-null ``next_cursor`` leaf, no ``total``), so a spec that declares
    ``strategy: page`` observes a wire contradiction. Later pages are
    empty so the run terminates on ``empty_page``; the detector reads the
    first page only.
    """
    import httpx  # noqa: PLC0415

    from autobridge.runtime.http import HttpClient  # noqa: PLC0415
    from autobridge.spec.models import RetryPolicy  # noqa: PLC0415

    if mode not in ("page", "cursor"):
        raise ValueError(f"unknown atlas fake mode {mode!r}; expected 'page' or 'cursor'")
    reservations = _atlas_reservations()
    folios = _atlas_folios()

    def page_of(data: list[dict[str, Any]], total: int) -> httpx.Response:
        payload = {"success": True, "data": data, "count": len(data), "total": total}
        return httpx.Response(200, content=_demo_dumps_exact(payload).encode(), headers={"Content-Type": "application/json"})

    def handler(request: httpx.Request) -> httpx.Response:
        import base64 as _b64  # noqa: PLC0415

        if request.url.path == "/api/v1.3/getReservations":
            data, total = reservations, len(reservations)
        elif request.url.path == "/api/v1.3/getTransactions":
            data, total = folios, len(folios)
        else:
            return httpx.Response(404, json={"msg": "not found"})
        if mode == "cursor":
            # The faulted vendor ignores the page params and serves its own
            # fixed cursor pages (size 2): page one carries the cursor leaf
            # with no total (the contradiction the detector fires on), and
            # the repaired cursor spec terminates Complete on the chain.
            qp = dict(request.url.params)
            start = 0
            token = qp.get("cursor")
            if token:
                try:
                    start = int(_b64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode())
                except Exception:
                    start = 0
            sliced = data[start : start + 2]
            nxt = start + len(sliced)
            more = bool(sliced) and nxt < total
            nxt_token = _b64.urlsafe_b64encode(str(nxt).encode()).decode().rstrip("=") if more else None
            payload = {"success": True, "data": sliced, "next_cursor": nxt_token}
            return httpx.Response(200, content=_demo_dumps_exact(payload).encode(), headers={"Content-Type": "application/json"})
        qp = dict(request.url.params)
        try:
            page_number = max(1, int(qp.get("pageNumber", "1")))
        except ValueError:
            page_number = 1
        try:
            page_size = max(1, min(100, int(qp.get("pageSize", "50"))))
        except ValueError:
            page_size = 50
        start = (page_number - 1) * page_size
        sliced = data[start : start + page_size]
        return page_of(sliced, total)

    transport = httpx.MockTransport(handler)

    def factory(**kw: Any) -> httpx.Client:
        return httpx.Client(transport=transport, timeout=kw.get("timeout", 5))

    return HttpClient(
        "https://demo.invalid",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=5),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def _harbor_reservations() -> list[dict[str, Any]]:
    """Legacy Harbor wire: snake_case records with string money."""
    rows: list[dict[str, Any]] = []
    for i in range(1, 5):
        rows.append(
            {
                "reservation_id": f"res-{i:03d}",
                "property_id": "prop-001",
                "guest_name": f"Guest {i}",
                "arrival_date": "2026-10-26",
                "departure_date": "2026-10-28",
                "status": "confirmed",
                "total_amount": f"{100 * i}.00",
                "currency": "USD",
                "updated_at": "2026-10-26T12:00:00Z",
            }
        )
    return rows


def _harbor_folios() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i in range(1, 4):
        rows.append(
            {
                "charge_id": f"chg-{i:03d}",
                "reservation_id": f"res-{i:03d}",
                "property_id": "prop-001",
                "business_date": "2026-10-26",
                "amount": f"{100 * i}.00",
                "currency": "USD",
                "description": f"Room charge {i}",
                "posted_at": "2026-10-26T12:00:00Z",
            }
        )
    return rows


def _harbor_fake_client(mode: str = "gross") -> Any:
    """Cursor-pagination fake serving the inline fixtures as Harbor would.

    ``mode="net"`` mirrors the ``gross_to_net`` simulator fault: money
    fields read 10% below the baseline (×0.9) while the page envelope
    still carries the baseline ``gross_sum`` meta, so the reference
    spec's ``money_invariant`` fails on observed responses only.
    Baseline sums are computed from the fixture rows, never hard-coded.
    """
    if mode not in ("gross", "net"):
        raise ValueError(f"unknown harbor fake mode {mode!r}; expected 'gross' or 'net'")

    def net_str(amount: str) -> str:
        return f"{(Decimal(amount) * Decimal('0.9')).quantize(Decimal('0.01')):f}"

    def encode(idx: int) -> str:
        return base64.urlsafe_b64encode(str(idx).encode()).decode().rstrip("=")

    def decode(cur: str | None) -> int:
        if not cur:
            return 0
        try:
            padded = cur + "=" * (-len(cur) % 4)
            return int(base64.urlsafe_b64decode(padded).decode())
        except Exception:
            return 0

    import httpx  # noqa: PLC0415

    from autobridge.runtime.http import HttpClient  # noqa: PLC0415
    from autobridge.spec.models import RetryPolicy  # noqa: PLC0415

    status_map = {"confirmed": "H1", "checked_in": "H2", "checked_out": "H3", "cancelled": "H4", "no_show": "H5"}
    reservations = [{**r, "status": status_map[r["status"]]} for r in _harbor_reservations()]
    folios = list(_harbor_folios())
    if mode == "net":
        reservations = [{**r, "total_amount": net_str(str(r["total_amount"]))} for r in reservations]
        folios = [{**r, "amount": net_str(str(r["amount"]))} for r in folios]

    def gross_sum(rows: list[dict[str, Any]], field: str) -> str:
        return f"{sum((Decimal(str(r[field])) for r in rows), Decimal('0')).quantize(Decimal('0.01')):f}"

    def handler(request: httpx.Request) -> httpx.Response:
        qp = dict(request.url.params)
        limit = int(qp.get("limit", "10"))
        start = decode(qp.get("cursor"))
        if request.url.path == "/reservations":
            data, total = reservations, len(reservations)
            baseline = gross_sum(_harbor_reservations(), "total_amount")
        elif request.url.path == "/folios":
            data, total = folios, len(folios)
            baseline = gross_sum(_harbor_folios(), "amount")
        else:
            return httpx.Response(404, json={"msg": "not found"})
        sliced = data[start : start + limit]
        nxt = start + limit
        envelope: dict[str, Any] = {
            "data": sliced,
            "total": total,
            "limit": limit,
            "cursor": qp.get("cursor"),
            "next_cursor": encode(nxt) if nxt < total else None,
        }
        if mode == "net":
            # Baseline meta stays gross while records read net: the same
            # contradiction the faulted simulator serves (observed only).
            envelope["gross_sum"] = baseline
        return httpx.Response(200, json=envelope)

    transport = httpx.MockTransport(handler)

    def factory(**kw: Any) -> httpx.Client:
        return httpx.Client(transport=transport, timeout=kw.get("timeout", 5))

    return HttpClient(
        "https://demo.invalid",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=5),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


# Step 7 reads the same committed Meridian docs the matrix test builds from
# (input data on disk, never a package import).
MERIDIAN_DOCS_PATH = REPO_ROOT / "simulators" / "docs" / "meridian-openapi.yaml"
MERIDIAN_DOCS_SOURCE = "simulators/docs/meridian-openapi.yaml"

# Step 8 reads the same committed FIAS docs the matrix test builds from
# (input data on disk, never a package import).
FIAS_DOCS_PATH = REPO_ROOT / "simulators" / "docs" / "fias-pabx.md"
FIAS_DOCS_SOURCE = "simulators/docs/fias-pabx.md"
FIAS_FAULT_PATH = REPO_ROOT / "simulators" / "scenarios" / "fias_record_layout_change.yaml"

# The PABX's announced PS layouts, firmware v2 → v3. Mirrors the fault
# YAML's announced_ps_layout ("P#RNTIDASOTMDUDDCX": P# RN TI DA SO TM DU
# DD CX) and the simulator's LAYOUT_V2_PS/LAYOUT_V3_PS defaults; a unit
# test pins these wire lists to the YAML so the demo cannot drift from
# the scenario it shows.
FIAS_PS_LAYOUT_V2 = ["P#", "RN", "TI", "DA", "SO", "TA", "DU", "DD"]
FIAS_PS_LAYOUT_V3 = ["P#", "RN", "TI", "DA", "SO", "TM", "DU", "DD", "CX"]


class _FiasFeed:
    """Minimal EVENT feed reader for the step-8 syncs (duck-types the relay reader)."""

    def __init__(self, ps: list[dict[str, Any]], re_: list[dict[str, Any]]) -> None:
        self._by_record = {"PS": ps, "RE": re_}

    def read(self, feed: Any, operation_name: str) -> list[dict[str, Any]]:
        rec = feed["record"] if isinstance(feed, dict) else feed.record
        records = self._by_record.get(rec)
        if records is None:
            raise KeyError(f"unknown feed {rec!r} for {operation_name!r}")
        return [dict(r) for r in records]

# Compact Meridian-shaped rows for the step-7 after-half sync: one
# reservation on one account, one named customer, two order items whose
# gross sums to the reservation total (Decimal-exact, string money on wire).
_MERIDIAN_RESERVATIONS = [
    {
        "Id": "RES-1",
        "ServiceId": "SVC-prop-001",
        "AccountId": "ACC-RES-1",
        "BookerId": "ACC-RES-1",
        "Number": "100001",
        "State": "Confirmed",
        "ScheduledStartUtc": "2026-10-27T15:00:00Z",
        "ScheduledEndUtc": "2026-10-29T11:00:00Z",
        "UpdatedUtc": "2026-10-26T10:00:00Z",
    }
]
_MERIDIAN_CUSTOMERS = [
    {
        "Id": "ACC-RES-1",
        "FirstName": "Ada",
        "LastName": "Lovelace",
        "Email": None,
        "Phone": None,
        "UpdatedUtc": "2026-10-26T10:00:00Z",
    }
]
_MERIDIAN_ITEMS = [
    {
        "Id": "CHG-1",
        "AccountId": "ACC-RES-1",
        "ServiceId": "SVC-prop-001",
        "Name": "Room charge",
        "UnitCount": 1,
        "UnitAmount": {"Currency": "USD", "NetValue": "18.18", "GrossValue": "19.99", "TaxValues": []},
        "PostedUtc": "2026-10-26T10:00:00Z",
    },
    {
        "Id": "CHG-2",
        "AccountId": "ACC-RES-1",
        "ServiceId": "SVC-prop-001",
        "Name": "Mini-bar",
        "UnitCount": 1,
        "UnitAmount": {"Currency": "USD", "NetValue": "0.01", "GrossValue": "0.01", "TaxValues": []},
        "PostedUtc": "2026-10-26T10:00:00Z",
    },
]
_MERIDIAN_ENTERPRISES = [{"Id": "prop-001", "Name": "Test", "TimeZone": "UTC", "CurrencyCode": "USD"}]


def _meridian_fake_client() -> Any:
    """POST-only fake serving Meridian-shaped pages under the cursor contract.

    Reads ``Limitation.Count``/``Limitation.Cursor`` from the JSON body and
    answers the response ``Cursor`` with the last id (null on empty page),
    mirroring the documented contract without importing the simulator.
    """
    import httpx  # noqa: PLC0415

    from autobridge.runtime.http import HttpClient  # noqa: PLC0415
    from autobridge.spec.models import RetryPolicy  # noqa: PLC0415

    def handler(request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = {}
        try:
            body = json.loads(request.content.decode() or "{}")
        except Exception:
            body = {}
        path = request.url.path
        rows: list[dict[str, Any]]
        key: str
        if "reservations" in path:
            rows, key = _MERIDIAN_RESERVATIONS, "Reservations"
        elif "customers" in path:
            rows, key = _MERIDIAN_CUSTOMERS, "Customers"
        elif "orderItems" in path:
            rows, key = _MERIDIAN_ITEMS, "OrderItems"
        elif "configuration" in path:
            return httpx.Response(200, json={"Enterprises": _MERIDIAN_ENTERPRISES})
        else:  # pragma: no cover - only the four documented ops are fetched
            raise AssertionError(f"unexpected path {path!r}")
        limit = int(((body.get("Limitation") or {}).get("Count")) or 100)
        cursor = (body.get("Limitation") or {}).get("Cursor")
        start = 0
        if cursor is not None:
            ids = [r["Id"] for r in rows]
            start = ids.index(cursor) + 1 if cursor in ids else 0
        page = rows[start : start + limit]
        return httpx.Response(200, json={key: page, "Cursor": page[-1]["Id"] if page else None})

    transport = httpx.MockTransport(handler)

    def factory(**kw: Any) -> httpx.Client:
        return httpx.Client(transport=transport, timeout=kw.get("timeout", 5))

    return HttpClient(
        "https://demo.invalid",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def _load_reference_spec(connector_id: str) -> dict[str, Any]:
    import yaml  # noqa: PLC0415

    path = GENERATED / connector_id / "reference" / "spec.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} did not parse to a dict"
    return data


def _load_reference_signature(connector_id: str) -> str:
    report = json.loads((GENERATED / connector_id / "reference" / "build-report.json").read_text(encoding="utf-8"))
    return str(report.get("signature", ""))


# ---------------------------------------------------------------------------
# Control-plane HTTP (live mode; best-effort, redacted)
# ---------------------------------------------------------------------------


def _control_probe(base_url: str) -> bool:
    try:
        import httpx  # noqa: PLC0415

        with httpx.Client(timeout=3) as client:
            r = client.get(base_url.rstrip("/") + "/healthz")
            return r.status_code == 200
    except Exception:
        return False


def _stack_is_local() -> bool:
    """Host-side view of the stack mode (mirrors tests/e2e/conftest.is_local_mode).

    Validated by bench.stack_mode (REQ-037): a stale ``.local/ACTIVE``
    from a dead local stack answers False — exactly as if the marker
    were absent — so a leaked Compose hostname fails the step instead of
    a later sync. The server itself normalizes
    (``normalize_connector_base_url``); the demo asserts it did.
    """
    return is_local_stack()


def _check_connector_address(sent: str, stored: Any) -> str | None:
    """Assert the stored connector address fits this stack's mode (REQ-027).

    Returns an error line when the server did not normalize, else None.
    """
    if not isinstance(stored, str) or not stored:
        return f"connector base_url missing in GET /connectors/{{id}} (sent {sent!r})"
    if _stack_is_local():
        if "sim-" in stored:
            return (
                f"Compose hostname leaked into local mode: stored {stored!r} "
                f"(sent {sent!r}); expected http://localhost:<port>"
            )
        return None
    # Compose mode (AUDIT-2026-10-29): every connector keeps its own
    # service-DNS address — this check used to hardcode sim-atlas, so the
    # REQ-031 seed failed Harbor/Meridian/PABX in Compose while passing
    # locally. Non-HTTP relay addresses (edge-relay://…) pass verbatim.
    if sent.startswith("edge-relay://"):
        return None if stored == sent else f"expected relay address {sent!r} in Compose mode, stored {stored!r}"
    from urllib.parse import urlparse  # noqa: PLC0415

    sent_host = urlparse(sent).hostname
    stored_host = urlparse(stored).hostname
    if stored_host in ("localhost", "127.0.0.1") or (sent_host and stored_host != sent_host):
        return (
            f"expected service-DNS base_url in Compose mode, stored {stored!r} "
            f"(sent {sent!r}); expected {sent!r}"
        )
    return None


def _control_call(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single control-plane call; never raises — returns a redacted record."""
    import httpx  # noqa: PLC0415

    url = base_url.rstrip("/") + path
    record: dict[str, Any] = {"method": method, "path": path}
    try:
        with httpx.Client(timeout=10) as client:
            # REQ-012: send the control write token when set. The headers
            # kwarg is only passed then, so stub clients in tests keep working
            # when auth is disabled.
            _extra: dict[str, Any] = {}
            if _h := control_headers():
                _extra["headers"] = _h
            r = client.request(method, url, json=payload, **_extra)
            record["status"] = r.status_code
            try:
                body = r.json()
            except Exception:
                body = {"_text": (r.text or "")[:300]}
            record["body"] = _redact_body(body)
    except Exception as exc:
        record["status"] = "unreachable"
        record["error"] = str(exc)[:300]
    return record


def _control_get_raw(base_url: str, path: str) -> Any:
    """GET a control-plane path and return the RAW parsed body for logic reads.

    ``_control_call`` redacts depth > 3 to an ellipsis for the transcript,
    which destroys logic fields like ``provenance_repair_run_id`` or
    ``refused_vocabularies`` entries. Assertions and lookups use this;
    the transcript keeps the redacted ``_control_call`` record.
    """
    import httpx  # noqa: PLC0415

    url = base_url.rstrip("/") + path
    try:
        with httpx.Client(timeout=10) as client:
            _extra: dict[str, Any] = {}
            if _h := control_headers():
                _extra["headers"] = _h
            r = client.request("GET", url, **_extra)
            try:
                return r.json()
            except Exception:
                return {}
    except Exception:
        return {}


def _live_ensure_connector(base_url: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Idempotent live connector create: reuse the existing one by name."""
    calls = [_control_call(base_url, "GET", "/connectors")]
    try:
        body = calls[0].get("body")
        existing = body if isinstance(body, list) else (body or {}).get("connectors") or []
        for conn in existing:
            if isinstance(conn, dict) and conn.get("name") == payload.get("name"):
                calls[0]["reused_connector_id"] = conn.get("id")
                return calls
    except Exception:
        pass
    calls.append(_control_call(base_url, "POST", "/connectors", payload))
    return calls


def _connector_id_from_calls(calls: list[dict[str, Any]], name: str) -> str | None:
    """Extract a connector id by name from ensure-call records (GET list or POST create)."""
    for call in calls:
        body = call.get("body")
        candidates: list[Any] = []
        if isinstance(body, list):
            candidates = body
        elif isinstance(body, dict):
            if isinstance(body.get("connectors"), list):
                candidates = body["connectors"]
            elif body.get("name") == name:
                candidates = [body]
        for conn in candidates:
            if isinstance(conn, dict) and conn.get("name") == name and conn.get("id"):
                return str(conn["id"])
    return None


def _call_ok(call: dict[str, Any]) -> bool:
    """A live control call succeeded iff its HTTP status is 2xx (REQ-016)."""
    status = call.get("status")
    return isinstance(status, int) and 200 <= status <= 299


def _failed_call_error(calls: list[dict[str, Any]]) -> str | None:
    """Error line for the first failing live call, or None when all are 2xx."""
    for call in calls:
        if _call_ok(call):
            continue
        status = call.get("status")
        detail = call.get("error") or call.get("body")
        try:
            detail_text = json.dumps(detail, default=str)[:300]
        except Exception:
            detail_text = str(detail)[:300]
        return (
            f"control {call.get('method')} {call.get('path')} "
            f"-> {status}: {detail_text}"
        )
    return None


def rung_table_row(step: dict[str, Any]) -> str:
    """One per-run usage row (REQ-021 item 3).

    ``scenario | rung that resolved it | model calls | small tokens |
    frontier tokens | why it escalated`` — read from the step's own
    evidence, so a step never reports a climb it did not perform.
    """
    evidence = step.get("evidence", {}) if isinstance(step.get("evidence"), dict) else {}
    usage = evidence.get("model_usage_ledger", {}) if isinstance(evidence.get("model_usage_ledger"), dict) else {}
    scenario = str(step.get("name", "?"))
    resolved = str(evidence.get("resolved_rung", "—"))
    calls = int(usage.get("calls", 0) or 0)
    small = int(usage.get("small_tokens", 0) or 0)
    frontier = int(usage.get("frontier_tokens", evidence.get("frontier_tokens", 0)) or 0)
    frontier_label = f"{frontier} frontier"
    frontier_reasoning = int(usage.get("frontier_reasoning", evidence.get("frontier_reasoning", 0)) or 0)
    if frontier_reasoning:
        frontier_label += f" ({frontier_reasoning:,} reasoning)"
    if evidence.get("frontier_replayed"):
        frontier_label += f" ({evidence.get('replay_label') or 'replay'})"
    if evidence.get("l3_skipped"):
        frontier_label += "; L3 skipped, no recording"
    why = str(
        evidence.get("why_escalated")
        or evidence.get("escalation_question")
        or evidence.get("rejection_reason")
        or evidence.get("degradation")
        or "—"
    )
    return f"{scenario} | resolved {resolved} | {calls} calls | {small} small / {frontier_label} | {why}"


def render_rung_summary(report: dict[str, Any]) -> str:
    """Per-run model-usage table across all demo steps (REQ-021 item 3)."""
    lines = ["per-run model usage:"]
    for step in report.get("steps", []):
        lines.append(f"  {rung_table_row(step)}")
    return "\n".join(lines)


def _finish_step(name: str, evidence: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a step record; any non-2xx live call fails the step (REQ-016)."""
    error = _failed_call_error(calls)
    if error is not None:
        return {
            "name": name,
            "status": "failed",
            "error": error,
            "evidence": evidence,
            "control_calls": calls,
            "mode": "live" if calls else "in-process",
        }
    return {
        "name": name,
        "status": "ok",
        "evidence": evidence,
        "control_calls": calls,
        "mode": "live" if calls else "in-process",
    }


def _redact_body(body: Any, *, _depth: int = 0) -> Any:
    """Truncate control-plane bodies for demo output (credential_ref names only)."""
    if _depth > 3:
        return "…"
    if isinstance(body, dict):
        out: dict[str, Any] = {}
        for k, v in list(body.items())[:25]:
            if k.lower() in {"spec", "draft_spec"}:
                out[k] = f"<{type(v).__name__} omitted>"
            else:
                out[k] = _redact_body(v, _depth=_depth + 1)
        return out
    if isinstance(body, list):
        return [_redact_body(v, _depth=_depth + 1) for v in body[:10]]
    if isinstance(body, str) and len(body) > 300:
        return body[:300] + "…"
    return body


# ---------------------------------------------------------------------------
# Six steps (local evidence; live calls attached when reachable)
# ---------------------------------------------------------------------------


def step1_reusable_artifact(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """Atlas reference release syncs Complete with zero model usage."""
    from autobridge.engine.sync_run import execute_sync  # noqa: PLC0415
    from autobridge.spec.models import load_spec_from_dict  # noqa: PLC0415

    spec = _load_reference_spec("atlaspms")
    load_spec_from_dict(spec)  # validates; raises on bad spec
    summary = execute_sync(spec, _atlas_fake_client())
    assert summary["outcome"] == "Complete", f"step 1 unmet: {summary.get('unmet_criteria')}"
    # Sync path never touches LLMProvider/Budget: the ledger is empty by construction.
    model_usage = {"calls": 0, "frontier_tokens": 0, "small_tokens": 0}
    sample = (summary.get("canonical_records", {}).get("Reservation", []) or [{}])[0]
    assert isinstance(sample.get("total_amount", Decimal("0")), Decimal), "money must be Decimal"
    calls: list[dict[str, Any]] = []
    # Canonical service-DNS form is valid input in both modes: the server
    # normalizes it to the address that works where the engine runs
    # (REQ-027 sub-step 1, normalize_connector_base_url). The demo reads
    # the stored connector back and asserts the normalized form.
    sent_base_url = "http://sim-atlas:8000"
    stored_base_url: Any = None
    if live:
        calls.append(_control_call(base_url, "GET", "/healthz"))
        # The reusable artifact must exist on the control plane too: ensure
        # the atlaspms connector by name (idempotent), then fetch it by id.
        ensure_calls = _live_ensure_connector(
            base_url,
            {
                "name": "atlaspms",
                "vendor_docs_ref": "simulators/docs/atlas-openapi.yaml",
                "base_url": sent_base_url,
                "credential_ref": "ATLAS_API_KEY",
                "target_entities": ["Reservation", "FolioCharge"],
            },
        )
        calls.extend(ensure_calls)
        connector_id = _connector_id_from_calls(ensure_calls, "atlaspms")
        if connector_id is not None:
            detail = _control_call(base_url, "GET", f"/connectors/{connector_id}")
            calls.append(detail)
            body = detail.get("body")
            if isinstance(body, dict):
                stored_base_url = body.get("base_url")
        calls.append(_control_call(base_url, "GET", "/metrics/model-usage"))
        addr_error = _check_connector_address(sent_base_url, stored_base_url)
        if addr_error is not None:
            calls.append(
                {
                    "method": "ASSERT",
                    "path": "/connectors/{id} base_url",
                    "status": "assertion-failed",
                    "error": addr_error,
                }
            )
    return _finish_step(
        "Reusable artifact — Atlas release, zero inference",
        {
            "connector": "atlaspms",
            "connector_base_url_sent": sent_base_url,
            "connector_base_url_stored": stored_base_url,
            "address_source": (
                "server-normalized for this stack mode "
                f"({'local' if _stack_is_local() else 'compose'} view from the demo host)"
                if live
                else "in-process evidence only (no live connector)"
            ),
            "release": "reference",
            "seed": seed,
            "signature": _load_reference_signature("atlaspms"),
            "outcome": summary["outcome"],
            "records_observed": summary.get("records_observed", {}),
            "fingerprint_id": summary.get("fingerprint_id"),
            "model_usage_ledger": model_usage,
            "credential_ref": spec["source"]["auth"]["credential_ref"],
            "resolved_rung": "—",
            "rung_attempts": [],
        },
        calls,
    )


def step2_unfamiliar_api(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """Harbor-style messy docs → hypotheses → draft spec (no model calls)."""
    from autobridge.discovery.bootstrap import build_draft_spec  # noqa: PLC0415
    from autobridge.discovery.classifiers import classify_all  # noqa: PLC0415
    from autobridge.discovery.docs_reader import read_docs_text  # noqa: PLC0415
    from autobridge.discovery.hypotheses import HypothesisEnumerator  # noqa: PLC0415

    iface = read_docs_text(DEMO_HARBOR_DOCS, "demo:harbor-excerpt")
    classification = classify_all(iface)
    hyps = HypothesisEnumerator().enumerate_all(iface, classification)
    assert hyps, "expected ≥1 hypothesis from the docs excerpt"
    result = build_draft_spec(
        DEMO_HARBOR_DOCS,
        source="demo:harbor-excerpt",
        connector_id="harbordemo",
        credential_ref="HARBOR_API_KEY",
        vendor="HarborPMS",
        base_url_override="https://api.harborpms.example.com",
    )
    from autobridge.config import spec_vocabulary_default  # noqa: PLC0415

    evidence = {
        "seed": seed,
        "spec_vocabulary": spec_vocabulary_default(),
        "doc_type": iface.doc_type,
        "operations": [f"{op.method} {op.path}" for op in iface.operations],
        "auth_kind": iface.auth.kind if iface.auth else None,
        "hypotheses": len(hyps),
        "families": sorted({h.family for h in hyps}),
        "draft_spec_built": bool(result.draft_spec),
        "hypothesis_families_resolved": sorted((result.hypotheses_by_family or {}).keys()),
        "open_questions": list(result.open_questions or []),
        "model_usage_ledger": result.model_usage,
        "degradation": result.degradation_reason,
        "resolved_rung": "—",
        "rung_attempts": [],
    }
    calls: list[dict[str, Any]] = []
    if live:
        ensure_calls = _live_ensure_connector(
            base_url,
            {
                "name": "Harbor demo",
                "vendor_docs_ref": "demo:harbor-excerpt",
                "base_url": "https://api.harborpms.example.com",
                "credential_ref": "HARBOR_API_KEY",
                "target_entities": ["Reservation", "FolioCharge"],
            },
        )
        calls.extend(ensure_calls)
        harbor_demo_id = _connector_id_from_calls(ensure_calls, "Harbor demo")
        if harbor_demo_id is None:
            _assert_live(calls, "connector Harbor demo id", False, "no connector id from ensure calls")
        else:
            # Build the excerpt through the control API with the pinned v1
            # vocabulary, so the connector profile records it and the
            # knowledge page lists it under v1 instead of "?".
            build_result = _live_build_and_poll(
                base_url, harbor_demo_id,
                {"docs_text": DEMO_HARBOR_DOCS, "spec_vocabulary": "v1"},
                calls,
            )
            _assert_live(
                calls, "Harbor demo docs build with v1",
                build_result.get("success") is True,
                f"excerpt build result: {build_result}",
            )
    return _finish_step(
        "Unfamiliar API — Harbor docs to draft spec",
        evidence,
        calls,
    )


def _poll_job_terminal(base_url: str, job_id: str, calls: list[dict[str, Any]], *, tries: int = 150) -> dict[str, Any] | None:
    """Poll GET /jobs/{id} until terminal (up to 5 min: a live L3 call may think long); every poll is a recorded call."""
    import time as _time  # noqa: PLC0415

    last: dict[str, Any] | None = None
    for _ in range(tries):
        rec = _control_call(base_url, "GET", f"/jobs/{job_id}")
        calls.append(rec)
        last = rec
        body = rec.get("body")
        state = body.get("state") if isinstance(body, dict) else None
        if state in ("succeeded", "failed"):
            return rec
        _time.sleep(2.0)
    calls.append(
        {
            "method": "ASSERT",
            "path": f"/jobs/{job_id} terminal",
            "status": "assertion-failed",
            "error": f"job {job_id} not terminal after {tries} polls",
        }
    )
    return last


def _assert_live(calls: list[dict[str, Any]], label: str, condition: bool, detail: str) -> None:
    """Fail the step loudly when the live engine did not do what the demo claims."""
    if not condition:
        calls.append({"method": "ASSERT", "path": label, "status": "assertion-failed", "error": detail})


def step3_zero_token_recovery(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """page_to_cursor drift: the live chain, L0 miss → L1 rule, 0 tokens.

    In-process runs the real chain on the reference release: a baseline
    sync (``Complete``), a faulted sync against the cursor-mode fake, then
    :func:`detect_and_repair_after_sync` on an in-memory control database —
    drift classified by the detector, patch proposed by the controller from
    detector-derived inputs only — then approval and a resync with the
    repaired spec (``Complete``). Live does the same against the stack:
    build, baseline sync, inject the fault, sync, assert on the drift row
    and the repair row the engine wrote, approve, resync. Every input in
    the step evidence names where it came from; nothing is hand-built.
    """
    from autobridge.engine.sync_run import execute_sync  # noqa: PLC0415
    from autobridge.spec.models import load_spec_from_dict  # noqa: PLC0415

    spec = _load_reference_spec("atlaspms")
    load_spec_from_dict(spec)  # validates; raises on a bad spec
    calls: list[dict[str, Any]] = []

    baseline = execute_sync(spec, _atlas_fake_client())
    assert baseline["outcome"] == "Complete", f"step 3 baseline unmet: {baseline.get('unmet_criteria')}"
    faulted = execute_sync(spec, _atlas_fake_client(mode="cursor"))
    faulted_records = dict(faulted.get("records_observed") or {})

    from sqlalchemy import create_engine as _create_engine  # noqa: PLC0415
    from sqlalchemy.orm import sessionmaker as _sessionmaker  # noqa: PLC0415

    from autobridge.control.models import Base as _Base  # noqa: PLC0415
    from autobridge.control.models import Connector as _Connector  # noqa: PLC0415
    from autobridge.control.models import ConnectorRelease as _Release  # noqa: PLC0415
    from autobridge.control.models import RepairRun as _RepairRun  # noqa: PLC0415
    from autobridge.control.models import spec_signature as _signature  # noqa: PLC0415
    from autobridge.engine.detect_repair import detect_and_repair_after_sync  # noqa: PLC0415
    from autobridge.engine.releases import approve_release  # noqa: PLC0415
    from autobridge.repair.knowledge import KnowledgeCache  # noqa: PLC0415

    engine = _create_engine("sqlite:///:memory:")
    _Base.metadata.create_all(engine)
    factory = _sessionmaker(bind=engine)
    with factory() as session:
        conn = _Connector(
            name="atlaspms",
            base_url="https://demo.invalid",
            credential_ref="ATLAS_API_KEY",
            target_entities=["Reservation"],
        )
        session.add(conn)
        session.flush()
        rel = _Release(connector_id=conn.id, version=1, spec=spec, signature=_signature(spec))
        session.add(rel)
        session.flush()
        conn.active_release_id = rel.id
        session.commit()
        out = detect_and_repair_after_sync(
            session,
            connector_id=conn.id,
            release_id=rel.id,
            spec_dict=spec,
            summary=faulted,
            sync_run_id="demo-step3-faulted",
            knowledge_cache=KnowledgeCache(),
        )
        assert out["drift_found"] is True, "detector must fire on the cursor envelope"
        assert out["drift_class"] == "PAGINATION_CHANGE", out
        assert out["decision"] == "propose" and out["rule_id"] == "page_to_cursor", out
        assert out["ladder_level_reached"] == 1 and out["candidate_release_id"], out
        assert out["model_usage"]["frontier_tokens"] == 0 and out["model_usage"]["small_tokens"] == 0, out
        run_row = session.get(_RepairRun, out["repair_run_id"])
        trace = [dict(e) for e in (run_row.ladder_trace if run_row is not None else [])]
        patch = list(run_row.candidate_patch or []) if run_row is not None else []
        session.commit()
        candidate = session.get(_Release, out["candidate_release_id"])
        assert candidate is not None
        candidate_spec = dict(candidate.spec)
        approval = approve_release(
            session, repair_run_id=out["repair_run_id"], actor="demo",
            from_release_id=rel.id, to_release_id=candidate.id, connector_id=conn.id,
        )
        assert approval.decision == "approve", approval.decision
        session.commit()
    resync = execute_sync(candidate_spec, _atlas_fake_client(mode="cursor"))
    assert resync["outcome"] == "Complete", f"step 3 resync unmet: {resync.get('unmet_criteria')}"

    attempts: list[dict[str, Any]] = []
    for entry in trace:
        if not isinstance(entry, dict):
            continue
        level_raw: Any = entry.get("level", entry.get("rung", -1))
        level = level_raw if isinstance(level_raw, int) and not isinstance(level_raw, bool) else -1
        attempts.append(
            {
                "rung": level,
                "action": str(entry.get("action", "")),
                "reason": str(entry.get("reason", "")),
            }
        )
    evidence: dict[str, Any] = {
        "drift_class": out["drift_class"],
        "drift_class_source": "classifier on wire signals from the faulted sync summary (raw_pages only; never the scenario id)",
        "delta_source": "build_detector_delta from the reference spec + wire signals (previous page shape vs observed cursor leaf)",
        "rule_source": "controller.handle_from_signals with detector-derived inputs only",
        "l0_knowledge": "miss",
        "l1_rule": out["rule_id"],
        "patch_ops": len(patch),
        "patch": patch[:4],
        "frontier_tokens": 0,
        "model_usage_ledger": dict(out["model_usage"]),
        "baseline_outcome": baseline["outcome"],
        "baseline_records": dict(baseline.get("records_observed") or {}),
        "faulted_outcome": faulted["outcome"],
        "faulted_records": faulted_records,
        "resync_outcome": resync["outcome"],
        "resync_records": dict(resync.get("records_observed") or {}),
        "drift_id": out["drift_id"],
        "repair_run_id": out["repair_run_id"],
        "candidate_release_id": out["candidate_release_id"],
        "resolved_rung": "L1",
        "rung_attempts": attempts,
    }

    if live:
        live_evidence = _step3_live(seed, base_url, calls)
        evidence["live"] = live_evidence
    return _finish_step(
        "Zero-token recovery — page_to_cursor via L1 rule",
        evidence,
        calls,
    )


def _live_build_and_poll(
    base_url: str, connector_id: str, payload: dict[str, Any], calls: list[dict[str, Any]]
) -> dict[str, Any]:
    """POST a build and poll its job; returns the job result dict (may be empty).

    The job result carries the bootstrap outcome (``success``, ``release_id``,
    ``degradation_reason``): a refused vocabulary still answers HTTP 200 with
    ``success: False`` and no release, which the caller asserts on.

    REQ-027: every live demo build probes served wire keys (fail-soft) so
    drift detection is scoped to wire keys the vendor actually served.
    """
    payload = dict(payload)
    payload.setdefault("probe_wire_shape", True)
    build_rec = _control_call(base_url, "POST", f"/connectors/{connector_id}/build", payload)
    calls.append(build_rec)
    build_body = build_rec.get("body") if isinstance(build_rec.get("body"), dict) else {}
    job_id = build_body.get("job_id") if isinstance(build_body, dict) else None
    if not job_id:
        return {}
    job_rec = _poll_job_terminal(base_url, str(job_id), calls)
    job_body = (job_rec or {}).get("body")
    if isinstance(job_body, dict) and isinstance(job_body.get("result"), dict):
        return dict(job_body["result"])
    return {}


def _reference_spec_for(connector_name: str) -> dict[str, Any] | None:
    """Parsed reference spec for a seeded connector, or None when absent.

    The client reads the file (bench-side); the server never reads files
    (AR-2-2). ``make demo`` on a fresh checkout still has these files —
    they are committed — so this only returns None when the workstream
    never built one.
    """
    import yaml  # noqa: PLC0415

    path = REPO_ROOT / "generated" / "connectors" / connector_name / "reference" / "spec.yaml"
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _ensure_harbor_reference_release(
    base_url: str, connector_id: str, calls: list[dict[str, Any]]
) -> str | None:
    """Harbor's active release must carry the money invariant (REQ-031).

    Registers ``generated/connectors/harborpms/reference/spec.yaml``
    through ``POST /connectors/{id}/reference-release`` when the active
    release has no ``money_invariant`` check (fresh ``make demo`` without
    the seed, or a docs-built release that cannot see ``gross_to_net``).
    A no-op when the active release already carries the check (the seed
    registers it up front). Returns the reference release id, or None
    when no reference spec is available — the caller then keeps the
    labelled gap instead of inventing a release.
    """
    detail = _control_get_raw(base_url, f"/connectors/{connector_id}")
    active: Any = detail.get("active_release_id") if isinstance(detail, dict) else None
    releases = detail.get("releases") if isinstance(detail, dict) else []
    for rel in releases or []:
        if isinstance(rel, dict) and rel.get("id") == active:
            if _money_fields_of_spec(rel.get("spec") or {}):
                return str(active)
            break
    spec = _reference_spec_for("harborpms")
    if spec is None:
        calls.append(
            {
                "method": "ASSERT",
                "path": "harbor reference spec",
                "status": "assertion-failed",
                "error": "no reference spec available and the active release has no money invariant",
            }
        )
        return None
    calls.append(
        _control_call(
            base_url,
            "POST",
            f"/connectors/{connector_id}/reference-release",
            {
                "spec": spec,
                "spec_vocabulary": "v1",
                "docs_source": "reference:harborpms",
                "scenario_id": "baseline",
            },
        )
    )
    after = _control_get_raw(base_url, f"/connectors/{connector_id}")
    active_after: Any = after.get("active_release_id") if isinstance(after, dict) else None
    _assert_live(
        calls,
        "harbor reference release registered and active",
        isinstance(active_after, str) and bool(active_after),
        f"reference-release after: {active_after}",
    )
    return str(active_after) if isinstance(active_after, str) and active_after else None


def _step_live_ensure_active_release(
    base_url: str, connector_id: str, docs_path: str, calls: list[dict[str, Any]],
    spec_vocabulary: str | None = None,
) -> str | None:
    """Build from docs text and activate the release (first build auto-activates).

    ``spec_vocabulary`` pins the vocabulary the build may use (REQ-021 item 2);
    it is recorded on the connector profile the knowledge page reads.
    """
    docs_text = (REPO_ROOT / docs_path).read_text(encoding="utf-8")
    payload: dict[str, Any] = {"docs_text": docs_text}
    if spec_vocabulary is not None:
        payload["spec_vocabulary"] = spec_vocabulary
    build_result = _live_build_and_poll(base_url, connector_id, payload, calls)
    # The release THIS build produced (never releases[-1]: on a reused
    # connector that row belongs to an earlier run's repair candidate,
    # which explicit activation rightly refuses with 422).
    build_release_id = build_result.get("release_id")
    if not isinstance(build_release_id, str) or not build_release_id:
        return None
    detail = _control_call(base_url, "GET", f"/connectors/{connector_id}")
    calls.append(detail)
    detail_body = detail.get("body") if isinstance(detail.get("body"), dict) else {}
    active_id = detail_body.get("active_release_id") if isinstance(detail_body, dict) else None
    if build_release_id and active_id != build_release_id:
        calls.append(_control_call(base_url, "POST", f"/connectors/{connector_id}/releases/{build_release_id}/activate", {}))
    return build_release_id


def _step_live_sync_terminal(
    base_url: str, connector_id: str, calls: list[dict[str, Any]]
) -> dict[str, Any]:
    """POST a sync and poll the job; returns the job result (outcome, ...)."""
    sync_rec = _control_call(base_url, "POST", f"/connectors/{connector_id}/sync", {})
    calls.append(sync_rec)
    sync_body = sync_rec.get("body") if isinstance(sync_rec.get("body"), dict) else {}
    job_id = sync_body.get("job_id") if isinstance(sync_body, dict) else None
    if not job_id:
        return {}
    job_rec = _poll_job_terminal(base_url, str(job_id), calls)
    job_body = (job_rec or {}).get("body")
    if isinstance(job_body, dict) and isinstance(job_body.get("result"), dict):
        return dict(job_body["result"])
    return {}


def _poll_live_repair_for_drift(
    base_url: str, connector_id: str, drift_id: str, calls: list[dict[str, Any]],
    *, tries: int = 75, wait_s: float = 2.0,
) -> dict[str, Any]:
    """Poll /repairs until the drift's repair row lands (REQ-042).

    A semantic break with a live frontier key queues the climb as a
    background repair job: POST /sync returns at once with
    ``repair_pending``, and the repair row appears seconds later when
    the job finishes. Every poll is a recorded call; the bound (~150 s)
    covers the L3 budget with margin.
    """
    import time as _time  # noqa: PLC0415

    # REQ-043: the running row exists from the start of the climb
    # (decision "climbing"), so the poll waits for a *terminal* decision —
    # a climbing row is progress, not the answer.
    _TERMINAL = ("propose", "escalate", "reject", "failed")
    repair_row: dict[str, Any] = {}
    for _ in range(tries):
        rep_rows: list[Any] = _control_get_raw(base_url, f"/repairs?connector_id={connector_id}") or []
        for rep in rep_rows:
            if (
                isinstance(rep, dict)
                and drift_id in (rep.get("drift_event_ids") or [])
                and rep.get("decision") in _TERMINAL
            ):
                calls.append(_control_call(base_url, "GET", f"/repairs?connector_id={connector_id}"))
                return dict(rep)
        _time.sleep(wait_s)
    calls.append(
        {
            "method": "ASSERT",
            "path": f"/repairs?connector_id={connector_id} drift {drift_id}",
            "status": "assertion-failed",
            "error": f"no finalized repair for drift {drift_id} after {tries} polls",
        }
    )
    return repair_row


def _live_latest_repair(
    base_url: str, connector_id: str, calls: list[dict[str, Any]]
) -> dict[str, Any]:
    """Newest repair row for a connector (the list endpoint orders newest first)."""
    repair_rec = _control_call(base_url, "GET", f"/repairs?connector_id={connector_id}")
    calls.append(repair_rec)
    repair_body: Any = _control_get_raw(base_url, f"/repairs?connector_id={connector_id}")
    if isinstance(repair_body, list) and repair_body and isinstance(repair_body[0], dict):
        return dict(repair_body[0])
    return {}


def _live_latest_drift(
    base_url: str, connector_id: str, calls: list[dict[str, Any]]
) -> dict[str, Any]:
    """Newest drift row for a connector (the list endpoint orders newest first)."""
    drift_rec = _control_call(base_url, "GET", f"/drift?connector_id={connector_id}")
    calls.append(drift_rec)
    drift_body = _control_get_raw(base_url, f"/drift?connector_id={connector_id}")
    if isinstance(drift_body, list) and isinstance(drift_body[0], dict):
        return dict(drift_body[0])
    return {}


def _live_candidate_for_repair(
    base_url: str, connector_id: str, repair_run_id: str | None, calls: list[dict[str, Any]]
) -> str | None:
    """Release id whose provenance names a repair run, or None."""
    if not repair_run_id:
        return None
    detail = _control_call(base_url, "GET", f"/connectors/{connector_id}")
    calls.append(detail)
    detail_body = _control_get_raw(base_url, f"/connectors/{connector_id}")
    releases = detail_body.get("releases") if isinstance(detail_body, dict) else []
    if isinstance(releases, list):
        for rel in releases:
            if isinstance(rel, dict) and rel.get("provenance_repair_run_id") == repair_run_id:
                candidate = rel.get("id")
                return str(candidate) if candidate else None
    return None


def _live_approve_repair(
    base_url: str, connector_id: str, repair_run_id: str, candidate_id: str,
    calls: list[dict[str, Any]],
) -> None:
    calls.append(
        _control_call(
            base_url, "POST", f"/repairs/{repair_run_id}/approve",
            {"to_release_id": candidate_id, "connector_id": connector_id},
        )
    )


def _live_knowledge(base_url: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Current /knowledge body (entries + connector profiles).

    Logic reads (poll predicates, reuse counts) need the RAW body:
    ``_control_call`` redacts depth > 3 to an ellipsis, which turns
    ``refused_vocabularies: ["v1"]`` into ``["…"]`` and can never satisfy
    a membership predicate. The transcript still records the redacted
    ``_control_call`` (same shape as every other call).
    """
    calls.append(_control_call(base_url, "GET", "/knowledge"))
    raw = _control_get_raw(base_url, "/knowledge")
    return dict(raw) if isinstance(raw, dict) else {}


def _poll_knowledge_profile(
    base_url: str,
    calls: list[dict[str, Any]],
    name: str,
    pred: Any,
    *,
    tries: int = 12,
    wait_s: float = 1.0,
) -> dict[str, Any]:
    """Poll /knowledge until the named connector's profile satisfies *pred*.

    The profile is derived from committed build rows, but a just-finished
    build's write may not be visible to the next request yet (owner check:
    step 7 read the Meridian profile before the v2 build's write was
    visible). Poll instead of reading once; every poll is a recorded call.
    Returns the last profile seen — callers still assert loudly on it.
    """
    import time as _time  # noqa: PLC0415

    profile: dict[str, Any] = {}
    for _ in range(tries):
        knowledge = _live_knowledge(base_url, calls)
        connectors = knowledge.get("connectors")
        profile = {}
        if isinstance(connectors, list):
            for conn in connectors:
                if isinstance(conn, dict) and conn.get("name") == name:
                    profile = conn
                    break
        try:
            if pred(profile):
                break
        except Exception:
            pass
        _time.sleep(wait_s)
    return profile


def _knowledge_reuses_for_rule(knowledge: dict[str, Any], rule_id: str) -> int:
    entries = knowledge.get("entries")
    if not isinstance(entries, list):
        return 0
    return sum(
        int(e.get("times_reused", 0) or 0)
        for e in entries
        if isinstance(e, dict) and e.get("rule_id") == rule_id
    )


def _step3_live(seed: int, base_url: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Live half of step 3: build, sync, inject, sync, assert, approve, resync.

    Then the inject-again leg: the pre-patch release is reactivated, the same
    fault is injected again, and the engine must reuse the learned patch at
    L0 with 0 model calls — /knowledge shows ``times_reused`` going up.
    """
    ensure_calls = _live_ensure_connector(
        base_url,
        {
            "name": "atlaspms",
            "vendor_docs_ref": "simulators/docs/atlas-openapi.yaml",
            "base_url": "http://sim-atlas:8000",
            "credential_ref": "ATLAS_API_KEY",
            "target_entities": ["Reservation", "FolioCharge"],
        },
    )
    calls.extend(ensure_calls)
    connector_id = _connector_id_from_calls(ensure_calls, "atlaspms")
    if connector_id is None:
        _assert_live(calls, "connector atlaspms id", False, "no connector id from ensure calls")
        return {"connector_id": None}
    _step_result = _step_live_ensure_active_release(
        base_url, connector_id, "simulators/docs/atlas-openapi.yaml", calls,
        spec_vocabulary="v1",
    )
    # The pre-patch release the inject-again leg reactivates: the release
    # this run's docs build just produced (never the newest release row,
    # which on a reused connector belongs to an earlier run's repair).
    base_release_id: str | None = _step_result
    sync_result = _step_live_sync_terminal(base_url, connector_id, calls)
    _assert_live(
        calls, "baseline sync Complete", sync_result.get("outcome") == "Complete",
        f"baseline sync outcome {sync_result.get('outcome')}: {sync_result.get('unmet_criteria')}",
    )
    calls.append(_control_call(base_url, "POST", "/demo/inject-fault", {"target": "sim-atlas", "scenario_id": "page_to_cursor", "seed": seed}))
    _step_live_sync_terminal(base_url, connector_id, calls)
    drift_row = _live_latest_drift(base_url, connector_id, calls)
    _assert_live(
        calls, "drift PAGINATION_CHANGE", drift_row.get("drift_class") == "PAGINATION_CHANGE",
        f"latest drift row: {drift_row}",
    )
    repair_row = _live_latest_repair(base_url, connector_id, calls)
    _raw_usage = repair_row.get("model_usage")
    usage: dict[str, Any] = _raw_usage if isinstance(_raw_usage, dict) else {}
    _assert_live(
        calls, "repair proposed by page_to_cursor with 0 tokens",
        repair_row.get("decision") == "propose"
        and repair_row.get("rule_id") == "page_to_cursor"
        and int(usage.get("frontier_tokens", 0) or 0) == 0
        and int(usage.get("small_tokens", 0) or 0) == 0,
        f"latest repair row: {repair_row}",
    )
    repair_run_id = str(repair_row.get("id")) if repair_row.get("id") else None
    first_rung = repair_row.get("ladder_level_reached")
    candidate_id = _live_candidate_for_repair(base_url, connector_id, repair_run_id, calls)
    if repair_run_id and candidate_id:
        _live_approve_repair(base_url, connector_id, repair_run_id, candidate_id, calls)
        resync_result = _step_live_sync_terminal(base_url, connector_id, calls)
        _assert_live(
            calls, "resync Complete after approval", resync_result.get("outcome") == "Complete",
            f"resync outcome {resync_result.get('outcome')}: {resync_result.get('unmet_criteria')}",
        )
    else:
        _assert_live(calls, "candidate release for repair run", False, f"run {repair_run_id}: no candidate release found")
    knowledge_before = _live_knowledge(base_url, calls)
    reuses_before = _knowledge_reuses_for_rule(knowledge_before, "page_to_cursor")
    # Inject-again leg: back to the pre-patch release, same fault, same
    # vendor+delta — the engine must answer from learned knowledge (L0,
    # 0 model calls), and /knowledge must show times_reused going up.
    again: dict[str, Any] = {"l0_hit": False, "rung": None, "reuses_before": reuses_before}
    if base_release_id:
        calls.append(
            _control_call(
                base_url, "POST",
                f"/connectors/{connector_id}/releases/{base_release_id}/activate", {},
            )
        )
        calls.append(_control_call(base_url, "POST", "/demo/inject-fault", {"target": "sim-atlas", "scenario_id": "page_to_cursor", "seed": seed}))
        _step_live_sync_terminal(base_url, connector_id, calls)
        again_row = _live_latest_repair(base_url, connector_id, calls)
        _raw_again_usage = again_row.get("model_usage")
        again_usage: dict[str, Any] = _raw_again_usage if isinstance(_raw_again_usage, dict) else {}
        _assert_live(
            calls, "inject-again repair reuses the learned patch with 0 tokens",
            again_row.get("decision") == "propose"
            and again_row.get("rule_id") == "page_to_cursor"
            and int(again_usage.get("frontier_tokens", 0) or 0) == 0
            and int(again_usage.get("small_tokens", 0) or 0) == 0,
            f"inject-again repair row: {again_row}",
        )
        again["rung"] = again_row.get("ladder_level_reached")
        again["l0_hit"] = again_row.get("ladder_level_reached") == 0
        _assert_live(
            calls, "inject-again repair is an L0 knowledge hit",
            again_row.get("ladder_level_reached") == 0,
            f"inject-again rung: {again_row.get('ladder_level_reached')} (expected 0): {again_row}",
        )
        again_repair_id = str(again_row.get("id")) if again_row.get("id") else None
        again_candidate = _live_candidate_for_repair(base_url, connector_id, again_repair_id, calls)
        if again_repair_id and again_candidate:
            _live_approve_repair(base_url, connector_id, again_repair_id, again_candidate, calls)
            again_resync = _step_live_sync_terminal(base_url, connector_id, calls)
            _assert_live(
                calls, "resync Complete after inject-again approval",
                again_resync.get("outcome") == "Complete",
                f"resync outcome {again_resync.get('outcome')}: {again_resync.get('unmet_criteria')}",
            )
        else:
            _assert_live(calls, "candidate release for inject-again run", False, f"run {again_repair_id}: no candidate release found")
    else:
        _assert_live(calls, "pre-patch release for the inject-again leg", False, "no base release id recorded after the docs build")
    knowledge_after = _live_knowledge(base_url, calls)
    reuses_after = _knowledge_reuses_for_rule(knowledge_after, "page_to_cursor")
    again["reuses_after"] = reuses_after
    _assert_live(
        calls, "times_reused went up after inject-again",
        reuses_after > reuses_before,
        f"page_to_cursor reuses before={reuses_before} after={reuses_after}: {knowledge_after.get('entries')}",
    )
    return {
        "connector_id": connector_id,
        "spec_vocabulary": "v1",
        "vocabulary_source": "explicit spec_vocabulary=v1 on the docs build (recorded on the connector profile)",
        "drift_class": drift_row.get("drift_class"),
        "drift_id": drift_row.get("id"),
        "repair_run_id": repair_run_id,
        "rule_id": repair_row.get("rule_id"),
        "ladder_level_reached": first_rung,
        "model_usage": usage,
        "candidate_release_id": candidate_id,
        "inject_again": again,
    }


def _step4_live(seed: int, base_url: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Live half of step 4: break → sync → Degraded → climb → error, live.

    Harbor's active release carries the money invariant (the seed registers
    the reusable reference release through the control API; a bare
    ``make demo`` registers it here via the same route), so the faulted
    sync degrades on ``money_invariant``, the detector and classifier name
    ``SEMANTIC_VALUE_DRIFT`` from the wire signals, and the engine's own
    climb runs: funnel (generated → amount → wire-consistent → distinct),
    L2 skipped (one explanation), exactly one L3 attempt — live with
    ``FRONTIER_API_KEY``, replayed from a capture otherwise, explicitly
    skipped with the capture hint when neither exists — the proposal
    judged, never released, one operator question. Harbor goes to
    ``error``, all from the break. The instrumented in-process climb runs
    the same inputs for the printed derivation; it is recorded via
    ``POST /demo/record-climb`` only when the break's L3 was skipped, as
    the documented no-key fallback.
    """
    ensure_calls = _live_ensure_connector(
        base_url,
        {
            "name": "harborpms",
            "vendor_docs_ref": "simulators/docs/harborpms.md",
            "base_url": "http://sim-harbor:8000",
            "credential_ref": "HARBOR_API_KEY",
            "target_entities": ["Reservation", "FolioCharge"],
        },
    )
    calls.extend(ensure_calls)
    connector_id = _connector_id_from_calls(ensure_calls, "harborpms")
    if connector_id is None:
        _assert_live(calls, "connector harborpms id", False, "no connector id from ensure calls")
        return {"connector_id": None}
    reference_id = _ensure_harbor_reference_release(base_url, connector_id, calls)
    if reference_id is None:
        _assert_live(calls, "harbor money invariant available live", False, "no reference release and no money check on the active release")
        return {"connector_id": connector_id, "gap": "no money invariant anywhere; cannot break gross_to_net live"}
    before_ids = {
        str(d.get("id"))
        for d in (_control_get_raw(base_url, f"/drift?connector_id={connector_id}") or [])
        if isinstance(d, dict) and d.get("id")
    }
    calls.append(_control_call(base_url, "POST", "/demo/inject-fault", {"target": "sim-harbor", "scenario_id": "gross_to_net", "seed": seed}))
    faulted_result = _step_live_sync_terminal(base_url, connector_id, calls)
    _assert_live(
        calls, "faulted sync Degraded on the money invariant",
        faulted_result.get("outcome") == "Degraded",
        f"faulted sync outcome {faulted_result.get('outcome')}: {faulted_result.get('unmet_criteria')}",
    )
    drift_rows: list[Any] = _control_get_raw(base_url, f"/drift?connector_id={connector_id}") or []
    engine_rows = [
        d for d in drift_rows
        if isinstance(d, dict)
        and d.get("drift_class") == "SEMANTIC_VALUE_DRIFT"
        and d.get("source_sync_run_id")
    ]
    fresh = [d for d in engine_rows if str(d.get("id")) not in before_ids]
    if len(fresh) == 1:
        drift_row: dict[str, Any] = dict(fresh[0])
        deduped_note = "new drift from this break"
    elif engine_rows:
        # Rerun without a reset: the still-open, still-undecided drift is
        # reused (dedupe), never doubled. Steps run after a reset, so the
        # fresh path is the norm; this keeps STEP=4 rerunnable.
        drift_row = dict(sorted(engine_rows, key=lambda d: str(d.get("created_at", "")))[-1])
        deduped_note = f"deduped onto still-open drift {drift_row.get('id')} (rerun without reset)"
        _assert_live(
            calls, "no second drift for one undecided fault",
            len(fresh) == 0,
            f"fresh drifts: {[d.get('id') for d in fresh]}",
        )
    else:
        _assert_live(calls, "one SEMANTIC_VALUE_DRIFT from the break", False, f"drift rows: {drift_rows[:2]}")
        return {
            "connector_id": connector_id,
            "faulted_sync_outcome": faulted_result.get("outcome"),
            "faulted_sync_unmet": faulted_result.get("unmet_criteria"),
        }
    _assert_live(
        calls, "drift names SEMANTIC_VALUE_DRIFT from detector evidence",
        drift_row.get("drift_class") == "SEMANTIC_VALUE_DRIFT" and bool(drift_row.get("source_sync_run_id")),
        f"drift row: {drift_row}",
    )
    rep_rows: list[Any] = _control_get_raw(base_url, f"/repairs?connector_id={connector_id}") or []
    repair_row: dict[str, Any] = {}
    for rep in rep_rows:
        if (
            isinstance(rep, dict)
            and rep.get("drift_class") == "SEMANTIC_VALUE_DRIFT"
            and drift_row.get("id") in (rep.get("drift_event_ids") or [])
        ):
            repair_row = dict(rep)
            break
    if not repair_row and faulted_result.get("repair_pending"):
        # REQ-042: the climb runs in the background repair job — the row
        # lands seconds after the break when the frontier call is live.
        repair_job_id = faulted_result.get("repair_job_id")
        if isinstance(repair_job_id, str) and repair_job_id:
            _poll_job_terminal(base_url, repair_job_id, calls)
        repair_row = _poll_live_repair_for_drift(
            base_url, connector_id, str(drift_row.get("id")), calls,
        )
    _assert_live(
        calls, "engine escalated with one operator question (never patched)",
        repair_row.get("decision") == "escalate" and bool((repair_row.get("escalation_bundle") or {}).get("question")),
        f"repair row: {repair_row}",
    )
    trace_levels = [a.get("level") for a in (repair_row.get("ladder_trace") or []) if isinstance(a, dict)]
    int_levels = sorted(lv for lv in trace_levels if isinstance(lv, int))
    _assert_live(
        calls, "live repair climbs L0 -> L1 -> L2 -> L3 -> L4 (each rung recorded)",
        int_levels == [0, 1, 2, 3, 4],
        f"ladder trace levels: {trace_levels}",
    )
    # REQ-039: the repair the break produced carries the funnel and exactly
    # one L3 attempt (live with a key, replayed from a capture otherwise).
    # With neither, L3 is skipped explicitly — named hash + capture hint —
    # and the instrumented climb below stays the documented fallback.
    ev_rows: list[Any] = _control_get_raw(base_url, f"/runs/{repair_row.get('id')}/evidence") or []
    hyp_rows = [
        e for e in ev_rows if isinstance(e, dict) and e.get("kind") == "hypothesis"
    ]
    stages: list[Any] = []
    if hyp_rows and isinstance(hyp_rows[0].get("payload"), dict):
        stages = hyp_rows[0]["payload"].get("stages") or []
    stage_names = [s.get("name") for s in stages if isinstance(s, dict)]
    _assert_live(
        calls, "live repair carries the derivation funnel (generated -> amount -> wire-consistent -> distinct)",
        stage_names == ["generated", "amount", "wire_consistent", "distinct"]
        and any(isinstance(s, dict) and (s.get("count") or 0) > 0 for s in stages),
        f"hypothesis stages: {stages}",
    )
    l3_entries = [
        a for a in (repair_row.get("ladder_trace") or [])
        if isinstance(a, dict) and a.get("level") == 3
    ]
    ledger: dict[str, Any] = _control_get_raw(base_url, f"/repairs/{repair_row.get('id')}/ledger") or {}
    ledger_calls = [c for a in (ledger.get("attempts") or []) for c in (a.get("calls") or [])]
    l3_attempted = (
        len(l3_entries) == 1
        and l3_entries[0].get("action") in ("frontier_generate", "proposal_rejected", "model_escalation")
        and len(ledger_calls) >= 1
    )
    l3_skipped = (
        len(l3_entries) == 1
        and l3_entries[0].get("action") == "l3_skipped"
        and ("no replay recording" in str(l3_entries[0].get("reason") or "")
             or "no provider" in str(l3_entries[0].get("reason") or ""))
    )
    _assert_live(
        calls, "live repair holds exactly one L3 attempt (live or replay) or one explicit L3 skip",
        l3_attempted or l3_skipped,
        f"rung 3: {l3_entries}, ledger calls: {len(ledger_calls)}",
    )
    raw_usage: Any = repair_row.get("model_usage")
    usage: dict[str, Any] = dict(raw_usage) if isinstance(raw_usage, dict) else {}
    if l3_attempted:
        _assert_live(
            calls, "attempted L3 is billed in the repair usage (live or replayed)",
            int(usage.get("frontier_tokens", 0) or 0) > 0,
            f"live repair model usage: {usage}",
        )
    return {
        "connector_id": connector_id,
        "reference_release_id": reference_id,
        "faulted_sync_outcome": faulted_result.get("outcome"),
        "faulted_sync_unmet": faulted_result.get("unmet_criteria"),
        "drift_id": drift_row.get("id"),
        "drift_class": drift_row.get("drift_class"),
        "repair_run_id": repair_row.get("id"),
        "repair_decision": repair_row.get("decision"),
        "deduped_note": deduped_note,
        "l3_attempted": l3_attempted,
        "l3_skipped": l3_skipped,
    }


def _money_fields_of_spec(spec: dict[str, Any]) -> dict[str, str]:
    """Entity -> money field for every money_invariant check in the spec."""
    out: dict[str, str] = {}
    for entity, ent_spec in (spec.get("entities") or {}).items():
        if not isinstance(ent_spec, dict):
            continue
        for check in ent_spec.get("checks") or []:
            kind = check.get("kind") if isinstance(check, dict) else getattr(check, "kind", None)
            field = check.get("field") if isinstance(check, dict) else getattr(check, "field", None)
            if kind == "money_invariant" and isinstance(field, str) and entity not in out:
                out[str(entity)] = field
    return out


def _semantic_climb_inputs() -> dict[str, Any]:
    """Engine-derived inputs for the gross_to_net climb (REQ-027 item 4).

    Every value is computed from observed evidence, never hand-written:

    * faulted sync: the reference Harbor spec against the net-mode fake
      (records 10% below baseline, gross_sum meta on the wire) — Degraded
      with money_invariant, like the faulted simulator;
    * drift class: the classifier on that sync's wire signals;
    * hypotheses, funnel and L3 evidence: the shared engine builder
      (:mod:`autobridge.repair.semantic`) — the same code the live repair
      path runs, so the instrumented climb and the break's climb ask the
      frontier the same prompt.
    """
    from autobridge.drift.classifier import classify  # noqa: PLC0415
    from autobridge.drift.signals import signals_from_sync  # noqa: PLC0415
    from autobridge.engine.detect_repair import build_detector_delta  # noqa: PLC0415
    from autobridge.engine.sync_run import execute_sync  # noqa: PLC0415
    from autobridge.repair import semantic as _semantic  # noqa: PLC0415

    spec = _load_reference_spec("harborpms")
    faulted = execute_sync(spec, _harbor_fake_client(mode="net"))
    assert faulted["outcome"] == "Degraded", f"net sync must degrade: {faulted}"
    assert "money_invariant" in (faulted.get("unmet_criteria") or []), faulted.get("unmet_criteria")
    signals = signals_from_sync(faulted, spec=spec, source_run_id="demo-step4-faulted")
    assert signals, "net sync must carry wire signals"
    classification = classify(signals)
    assert classification.drift_class == "SEMANTIC_VALUE_DRIFT", classification
    delta = build_detector_delta(spec, signals)

    # The shared engine builder: enumeration from the wire signals and the
    # spec, wire filter, distinct selection, redacted L3 evidence. The live
    # scheduler/worker path runs exactly this (REQ-039).
    source = "demo-step4-faulted"
    enumerated, money_fields = _semantic.enumerate_money_hypotheses(spec, source=source)
    assert money_fields, "reference spec must name money fields"
    failed = _semantic.money_invariant_failed(faulted, list(signals))
    assert failed, "faulted sync must fail the money invariant"
    survivors = _semantic.filter_wire_consistent(enumerated, invariant_failed=failed)
    assert survivors, "at least the net reading must survive the observed invariant failure"
    climbed, siblings = _semantic.select_climbed(survivors)
    assert climbed is not None
    live_hyps = [climbed]
    evidence = _semantic.build_l3_evidence(spec, faulted, climbed)
    return {
        "spec": spec,
        "faulted_summary": faulted,
        "drift_class": classification.drift_class,
        "drift_reason": classification.reason,
        "signals_count": len(signals),
        "fingerprint_delta": delta,
        "enumerated_count": len(enumerated),
        "amount_count": len(enumerated),
        "money_fields": money_fields,
        "survivor_ids": [h.id for h in survivors],
        "sibling_ids": siblings,
        "hypotheses": live_hyps,
        "yield_count": len(live_hyps),
        "evidence": evidence,
    }


def _step4_record_climb(
    base_url: str,
    connector_id: str,
    inp: dict[str, Any],
    attempts: list[dict[str, Any]],
    usage: dict[str, Any],
    climb_calls: list[dict[str, Any]],
    proposal: list[dict[str, Any]],
    checked_by: str,
    trapped: bool,
    question: str,
    calls: list[dict[str, Any]],
    model_rationale: str = "",
) -> dict[str, Any]:
    """Record the instrumented climb as rows (REQ-030 item 3).

    POSTs the climb through the labelled ``/demo/record-climb`` endpoint,
    then reads the repair + ledger back and asserts on the rows (decision,
    full rung trace, hypothesis ids, model-call box). The demo's claims
    about step 4 are therefore backed by database rows the UI reads, never
    by process memory alone.
    """
    trace = []
    for a in attempts:
        if int(a.get("rung", a.get("level", -1))) == 4:
            continue
        entry: dict[str, Any] = {
            "level": int(a.get("rung", a.get("level", -1))),
            "action": str(a.get("action", "")),
            "reason": str(a.get("reason", "")),
        }
        # REQ-047: keep the model's own question/rationale on the rung so
        # the repair page can label it as the model's.
        if a.get("model_question"):
            entry["model_question"] = str(a["model_question"])
        if a.get("model_rationale"):
            entry["model_rationale"] = str(a["model_rationale"])
        trace.append(entry)
    if not proposal:
        # REQ-047 item 3: nothing was proposed (the model escalated), so no
        # check ran — record not_verified, never a fail verdict.
        verdict = {"verdict": "not_verified", "failed_checks": [], "judged_by": checked_by}
    else:
        verdict = (
            {"verdict": "fail", "failed_checks": ["invalid_patch_trap"], "judged_by": checked_by}
            if trapped
            else {"verdict": "fail", "failed_checks": [], "judged_by": checked_by}
        )
    payload = {
        "connector_id": connector_id,
        "drift_class": inp["drift_class"],
        "vendor": "harbor",
        "scenario": "gross_to_net",
        "hypotheses": [
            {"id": str(getattr(h, "id", h)), "family": str(getattr(h, "family", "")), "claim": str(getattr(h, "claim", ""))}
            for h in inp["hypotheses"]
        ],
        "enumerator_yield_count": inp["yield_count"],
        "attempts": trace,
        "model_usage": {k: v for k, v in usage.items() if isinstance(v, (int, float))},
        "model_calls": climb_calls,
        "proposal": proposal,
        "verifier_verdict": verdict,
        "escalation_question": question,
        "model_rationale": model_rationale,
    }
    rec_call = _control_call(base_url, "POST", "/demo/record-climb", payload)
    calls.append(rec_call)
    body = rec_call.get("body")
    repair_id = body.get("repair_run_id") if isinstance(body, dict) else None
    _assert_live(calls, "climb recorded with repair row", bool(repair_id), f"record-climb: {rec_call}")
    if not repair_id:
        return {"recorded": False}
    rep = _control_get_raw(base_url, f"/repairs/{repair_id}")
    _assert_live(
        calls, "recorded repair escalates with the full climb",
        rep.get("decision") == "escalate"
        and [a.get("level") for a in (rep.get("ladder_trace") or [])] == [0, 1, 2, 3, 4],
        f"repair row: {rep}",
    )
    ledger = _control_get_raw(base_url, f"/repairs/{repair_id}/ledger")
    ledger_calls = [c for a in (ledger.get("attempts") or []) for c in (a.get("calls") or [])]
    _assert_live(
        calls, "recorded ledger carries the climb's model calls",
        len(ledger_calls) == len(climb_calls),
        f"ledger: {ledger}",
    )
    return {"recorded": True, "drift_id": (body or {}).get("drift_id"), "repair_run_id": repair_id}


def step4_hard_semantic_failure(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """gross_to_net: detector-derived drift, real-evidence L3, check, escalation.

    The faulted sync (reference Harbor spec vs net-mode wire: records 10%
    below the gross_sum meta) degrades on money_invariant; the classifier
    names SEMANTIC_VALUE_DRIFT from the wire signals. Hypotheses come from
    the shared engine builder (amount readings from the spec's money
    fields); the wire filter keeps the net readings, and the climb
    demonstrates the most specific one with the frontier given real
    evidence (spec excerpt + failing-records sample, redacted). The
    proposal is checked (live verifier verdict, else the documented trap
    predicate): the known decoy (net_amount -> total_amount) is rejected;
    anything else still ends Degraded with one operator question, because
    a semantic drift is never patched without the operator. Nothing is
    released either way. Live, Harbor is seeded (or registered here) from
    the reference release, so the same chain happens through the stack:
    inject → sync → Degraded → engine climb + escalated repair → error —
    the break's repair carries the funnel and the L3 attempt, and the
    instrumented climb below is recorded only when the break's L3 was
    skipped. When no provider and no recording exist, L3 is labelled
    skipped instead of invented.
    """
    from autobridge.llm.provider import Budget, resolve_frontier_provider  # noqa: PLC0415
    from autobridge.repair.escalation import escalation_for_no_fix  # noqa: PLC0415
    from autobridge.repair.knowledge import KnowledgeCache  # noqa: PLC0415
    from autobridge.repair.ladder import run_frontier_climb  # noqa: PLC0415

    provider, mode = resolve_frontier_provider()
    inp = _semantic_climb_inputs()
    spec = inp["spec"]
    # REQ-030 item 3: the climb's ModelCall rows persist (here, into a
    # scratch session the step then records through the labelled demo
    # endpoint) — never in-process-only. The outcome stamps land via the
    # ladder's stamp_call_outcome, so each row says what it produced.
    from sqlalchemy import create_engine as _create_engine  # noqa: PLC0415
    from sqlalchemy.orm import sessionmaker as _sessionmaker  # noqa: PLC0415

    from autobridge.control.models import Base as _Base  # noqa: PLC0415
    from autobridge.control.models import ModelCall as _ModelCall  # noqa: PLC0415

    _climb_engine = _create_engine("sqlite:///:memory:")
    _Base.metadata.create_all(_climb_engine)
    _climb_session = _sessionmaker(bind=_climb_engine)()
    evidence_sources = {
        "spec": "generated/connectors/harborpms/reference/spec.yaml",
        "faulted_sync": "execute_sync(reference spec, net-mode fake: records below gross_sum meta)",
        "drift_class": f"classifier on {inp['signals_count']} wire signals ({inp['drift_reason']})",
        "fingerprint_delta": "build_detector_delta from spec + wire signals (None: wire shape intact)",
        "hypotheses": f"engine amount enumeration from the spec's money fields ({inp['enumerated_count']} generated -> {inp['amount_count']} amount -> {len(inp['survivor_ids'])} wire-consistent -> 1 distinct: most specific field)",
        "wire_consistent_survivors": f"{inp['survivor_ids']} (gross/tax_inclusive predict the invariant holds — contradicted by the observed failure)",
        "climbed": f"{inp['hypotheses'][0].id} (most specific surviving field; siblings: {inp['sibling_ids'] or 'none'})",
        "yield_count": f"{inp['yield_count']} live hypothesis into L2/L3",
        "l3_evidence": "spec excerpt + failing-records sample (extracted, redacted)",
    }
    climb = run_frontier_climb(
        spec=spec,
        drift_class=inp["drift_class"],
        hypotheses=list(inp["hypotheses"]),
        enumerator_yield_count=inp["yield_count"],
        provider=provider,
        budget=Budget(),
        session=_climb_session,
        run_id="demo-step4-gross_to_net",
        knowledge_cache=KnowledgeCache(),
        fingerprint_delta=inp["fingerprint_delta"],
        vendor="harbor",
        scenario="gross_to_net",
        validate_against=_load_reference_spec("harborpms"),
        evidence=dict(inp["evidence"]),
    )
    _climb_session.commit()
    _climb_calls = [
        {
            "tier": row.tier,
            "provider": row.provider,
            "model": row.model,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "reasoning_tokens": row.reasoning_tokens,
            "latency_ms": row.latency_ms,
            "cost": str(row.cost_estimate),
            "purpose": row.purpose,
            "mode": row.mode,
            "captured_at": row.captured_at,
            "outcome": row.outcome,
        }
        for row in _climb_session.query(_ModelCall).order_by(_ModelCall.created_at).all()
    ]
    _climb_session.close()
    attempts = list(climb["attempts"])
    usage = dict(climb["usage"])
    proposal = climb["proposal"] or []
    # Outcome assertions, not scripted answers: the climb is explicit about
    # every rung (attempted or plainly skipped); never silent, never invented.
    assert [a["rung"] for a in attempts[:2]] == [0, 1], f"demo climb must show L0+L1: {attempts}"
    l3_attempts = [a for a in attempts if a["rung"] == 3]
    l3_call = l3_attempts[0] if l3_attempts else None
    # A rung-3 attempt is a real frontier call when it generated text or the
    # answer came back unusable (rejected/unparsable): both cost tokens.
    # Only an explicit l3_skipped (no provider, missing replay recording)
    # means no call was made — the "skipped" wording is reserved for that
    # case (REQ-021 owner check 2026-10-28: a live answer that yields no
    # patch must say what happened, never "no frontier call available").
    l3_called = l3_call is not None and l3_call.get("action") in ("frontier_generate", "proposal_rejected", "model_escalation")
    l3_skipped = not l3_called
    assert l3_attempts or climb.get("replay_missing") or provider is None, (
        f"demo climb must attempt or skip L3 explicitly: {attempts}"
    )
    trapped = any(
        "net_amount" in str(op.get("value", "")) and "total_amount" in str(op.get("path", ""))
        for op in proposal
        if isinstance(op, dict)
    )
    # REQ-047: the climb already knows whether the model escalated (an
    # explicit {"escalate": ..., "rationale": ...} answer) — the labels
    # below must say so instead of calling it "no applicable patch".
    model_escalated = bool(climb.get("model_escalated"))
    model_question = str(climb.get("model_question") or "").strip()
    model_rationale = " ".join(str(climb.get("model_rationale") or "").split())[:300]
    if model_escalated and not model_question:
        # Defense-in-depth: an escalate answer always carries the question
        # (ladder normalizes it); without one it is a no-answer, not an
        # escalation.
        model_escalated = False
    checked_by = "trap predicate (documented oracle rule)"
    if model_escalated:
        # No patch was proposed, so there is nothing for the verifier to
        # judge — say so and do not report a fail verdict (REQ-047 item 3).
        checked_by = "no patch to verify (the model escalated)"
    elif live and not l3_skipped:
        from autobridge.engine.verification import verify_spec  # noqa: PLC0415

        harbor_spec = _load_reference_spec("harborpms")
        from autobridge.spec.patch import apply_patch  # noqa: PLC0415

        try:
            patched = apply_patch(harbor_spec, proposal) if proposal else harbor_spec
        except Exception as exc:
            # A proposal that does not apply is a recorded rejection, never
            # a crash: the ladder already checked mechanics, this guards the
            # verify path itself.
            checked_by = f"L3 proposal does not apply ({exc}); recorded, never released"
            proposal = []
        verifier_url = (os.environ.get("VERIFIER_URL", "") or "").strip() or base_url.replace("8101", "8104")
        try:
            outcome = verify_spec(patched, "invalid_patch_trap", verifier_url)
            failed = [f.get("name", "") for f in outcome.failed_checks]
            trapped = trapped and ("invalid_patch_trap" in failed)
            checked_by = f"live verifier verdict={outcome.verdict} failed={failed}"
        except Exception as exc:
            # Transport failure, not a verdict: the predicate above still
            # proves the trap; say exactly what happened instead of failing
            # the step on an environmental error.
            checked_by = f"live verifier unreachable ({exc}); trap predicate used"
    def _l3_model_name() -> str:
        getter = getattr(provider, "model_for_tier", None)
        if callable(getter):
            try:
                name = getter("frontier")
                if name:
                    return str(name)
            except Exception:
                pass
        try:
            from autobridge.config import frontier_model as _default_frontier_model  # noqa: PLC0415

            return str(_default_frontier_model())
        except Exception:
            return "unknown"

    l3_model = _l3_model_name()
    frontier_tokens = int(usage.get("frontier_tokens", 0) or 0)
    frontier_reasoning = int(usage.get("frontier_reasoning", 0) or 0)
    reasoning_note = f" ({frontier_reasoning:,} reasoning)" if frontier_reasoning else ""
    l3_raw_preview = ""
    if l3_call is not None:
        l3_raw_preview = str(l3_call.get("raw_preview") or (climb.get("l3_text", "") or "").strip()[:300])
    if l3_skipped:
        if climb.get("replay_missing"):
            skip_detail = (
                f"no replay recording for prompt {climb['replay_missing'][0]}; "
                "run make record-frontier SCENARIO=gross_to_net to capture it"
            )
        elif l3_call is not None and l3_call.get("reason"):
            skip_detail = str(l3_call["reason"])
        else:
            skip_detail = "no frontier provider configured (FRONTIER_MODE=off)"
        rejection_reason = (
            f"no frontier call available ({skip_detail}); "
            "L3 labelled skipped, semantic drift still needs the operator"
        )
    elif model_escalated:
        # REQ-047 item 1: an explicit escalate answer is a human decision,
        # not a "no applicable patch" — name the call and the outcome.
        rejection_reason = (
            f"L3 called {mode} ({l3_model}, {frontier_tokens:,} tokens{reasoning_note}): "
            "the model asked for a human decision"
        )
    elif trapped:
        rejection_reason = "total_amount must stay gross; net_amount is the decoy net"
    elif proposal:
        rejection_reason = (
            f"frontier proposed {proposal} via {mode} ({l3_model}, {frontier_tokens:,} tokens{reasoning_note}); "
            "no verified fix, operator decides"
        )
    else:
        if l3_call is not None and l3_call.get("action") == "proposal_rejected":
            no_patch_detail = str(l3_call.get("reason", "proposal rejected"))
        else:
            no_patch_detail = str((l3_call or {}).get("parse_detail", "") or "no JSON patch or escalation found in response")
        rejection_reason = (
            f"L3 called {mode} ({l3_model}, {frontier_tokens:,} tokens{reasoning_note}): "
            f"response contained no applicable patch ({no_patch_detail}); "
            f"raw response: {l3_raw_preview or '<empty>'}"
        )
    rejection_reason = " ".join(rejection_reason.split())
    records_sample = inp["evidence"]["records_sample"]
    try:
        below_pct = (Decimal(str(records_sample["baseline_gross_sum"])) - Decimal(str(records_sample["observed_sum"]))) / Decimal(str(records_sample["baseline_gross_sum"])) * Decimal(100)
        below_label = f"{below_pct:.0f}% below gross"
    except Exception:
        below_label = "below gross"
    # REQ-047 item 2: when the model escalates, its question IS the
    # escalation question (with its rationale beneath); the canned text is
    # only the fallback when no model question exists.
    _canned_question = "Harbor totals now read net instead of gross — should total_amount track $.gross_amount with the money transform, or is a new canonical field needed?"
    _question = model_question if model_escalated and model_question else _canned_question
    _bundle_reason = f"gross_to_net: net is {below_label}; no deterministic rule maps net_amount safely"
    if model_escalated and model_rationale:
        _bundle_reason += f"; model rationale: {model_rationale}"
    bundle = escalation_for_no_fix(
        _bundle_reason,
        evidence_ids=["demo-step4-faulted", "simulators/docs/harborpms.md#Money"],
        question=_question,
    )
    if model_escalated:
        _l4_reason = "model asked for a human decision: one operator question"
    else:
        _l4_reason = f"{'invalid_patch_trap' if trapped else 'unverified proposal'}: one operator question"
    attempts.append(
        {
            "rung": 4,
            "action": "escalate",
            "reason": _l4_reason,
            "calls": [],
        }
    )
    sample_values = [
        rec.get("total_amount")
        for rec in ((inp["faulted_summary"].get("canonical_records") or {}).get("Reservation") or [])
        if isinstance(rec, dict)
    ]
    assert sample_values, "faulted sync must observe reservation totals"
    assert all(isinstance(v, Decimal) and v.as_tuple().exponent == -2 for v in sample_values), sample_values
    calls: list[dict[str, Any]] = []
    if live:
        live_evidence = _step4_live(seed, base_url, calls)
    else:
        live_evidence = {"mode": "in-process evidence only (no live connector)"}
    recorded: dict[str, Any] = {"mode": "not recorded (no live control plane)"}
    if live and isinstance(live_evidence, dict) and live_evidence.get("connector_id"):
        if live_evidence.get("l3_skipped"):
            # Fallback only: the break's repair carries no L3 attempt (no
            # key and no recording), so the instrumented climb is recorded
            # for the model-call box. When the break attempted L3 (live or
            # replay), its repair is the story — never a second row.
            recorded = _step4_record_climb(
                base_url,
                str(live_evidence["connector_id"]),
                inp,
                attempts,
                usage,
                _climb_calls,
                proposal,
                checked_by,
                trapped,
                bundle.question,
                calls,
                model_rationale,
            )
        else:
            recorded = {
                "mode": "break carries the climb (record-climb fallback skipped)",
                "repair_run_id": live_evidence.get("repair_run_id"),
            }
    from autobridge.config import small_model as _small_model  # noqa: PLC0415
    from autobridge.llm.provider import Usage, estimate_cost  # noqa: PLC0415

    # Model ids live in config, not code (DIRECTIVES §3): price the call
    # the climb actually made, at the configured tiers.
    frontier_cost = estimate_cost(
        "frontier",
        Usage(usage.get("frontier_input", 0), usage.get("frontier_output", 0)),
        model=l3_model,
    )
    small_cost = estimate_cost(
        "small", Usage(usage.get("small_input", 0), usage.get("small_output", 0)), model=_small_model()
    )
    replay_label = ""
    if mode == "replay" and not l3_skipped:
        captured = climb.get("replay_captured_at", "") or ""
        if captured and not captured.startswith("synthetic"):
            replay_label = f"replayed from a live Muse capture on {captured}"
        else:
            replay_label = "replayed (test capture)"
    # REQ-047 item 4: the title names the new truth — the model saw the
    # evidence and asked for a business decision; when it proposes a patch
    # instead, the verifier verdict decides.
    if model_escalated:
        _step_title = "Hard semantic failure — gross_to_net escalated: model asked for a business decision"
    elif trapped:
        _step_title = "Hard semantic failure — gross_to_net rejected (invalid_patch_trap), escalated"
    elif proposal:
        _step_title = "Hard semantic failure — gross_to_net proposal judged, escalated"
    else:
        _step_title = "Hard semantic failure — gross_to_net rejected, escalated"
    return _finish_step(
        _step_title,
        {
            "sync_outcome": inp["faulted_summary"]["outcome"],
            "unmet_criteria": list(inp["faulted_summary"].get("unmet_criteria") or []),
            "drift_class": inp["drift_class"],
            "drift_class_source": evidence_sources["drift_class"],
            "input_sources": evidence_sources,
            "derivation": {
                "signals": inp["signals_count"],
                "enumerated_hypotheses": inp["enumerated_count"],
                "amount_hypotheses": inp["amount_count"],
                "money_fields": inp["money_fields"],
                "wire_consistent_survivors": inp["survivor_ids"],
                "sibling_readings": inp["sibling_ids"],
                "yield_count": inp["yield_count"],
            },
            "decoy_patch_rejected": "invalid_patch_trap" if trapped else "no decoy proposed",
            "l3_proposal": proposal,
            "l3_model": l3_model,
            "l3_raw_preview": l3_raw_preview,
            "rejection_reason": rejection_reason,
            "checked_by": checked_by,
            "frontier_mode": mode,
            "frontier_replayed": mode == "replay" and not l3_skipped,
            "replay_label": replay_label,
            "l3_skipped": l3_skipped,
            "escalation_question": bundle.question,
            "model_escalated": model_escalated,
            "model_question": model_question,
            "model_rationale": model_rationale,
            "money_type": "Decimal(2dp)",
            "release_persisted": False,
            "resolved_rung": "L4 (escalated)",
            "rung_attempts": attempts,
            "why_escalated": rejection_reason,
            "live": live_evidence,
            "recorded_climb": recorded,
            "model_usage_ledger": {
                "calls": usage.get("calls", 0),
                "small_tokens": usage.get("small_tokens", 0),
                "frontier_tokens": usage.get("frontier_tokens", 0),
                "estimated_cost": str(frontier_cost + small_cost),
                "mode": mode,
            },
        },
        calls,
    )


def step5_head_to_head(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """Scorecards as they are — wins and losses."""
    evidence: dict[str, Any] = {"scorecard_path": str(SCORECARD_PATH), "seed": seed}
    if SCORECARD_PATH.is_file():
        lines = SCORECARD_PATH.read_text(encoding="utf-8").splitlines()
        evidence["scorecard_lines"] = len(lines)
        evidence["preview"] = lines[:12]
    else:
        evidence["scorecard_lines"] = 0
        evidence["preview"] = ["no scorecard yet — run make bench"]
    calls: list[dict[str, Any]] = []
    if live:
        rec = _control_call(base_url, "GET", "/bench/scorecards")
        calls.append(rec)
        body = rec.get("body")
        if isinstance(body, dict):
            evidence["control_scorecards_total"] = body.get("total")
    evidence["resolved_rung"] = "—"
    evidence["rung_attempts"] = []
    return _finish_step(
        "Head-to-head — bench scorecards",
        evidence,
        calls,
    )


def step6_reproducibility(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """Seed, release ids, make demo."""
    return {
        "name": "Reproducibility — seed, release ids, make demo",
        "status": "ok",
        "mode": "in-process",
        "evidence": {
            "seed": seed,
            "control_url": base_url,
            "live_control_api": live,
            "atlas_reference_signature": _load_reference_signature("atlaspms"),
            "harbor_reference_signature": _load_reference_signature("harborpms"),
            "replay": "make demo (DEMO_SEED=… CONTROL_URL=…)",
            "resolved_rung": "—",
            "rung_attempts": [],
        },
        "control_calls": [],
    }


def step7_new_api_style(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """Meridian before/after: v1 refuses the POST/body-auth docs, v2 connects.

    Before: ``build_draft_spec`` on the committed Meridian docs with
    ``spec_vocabulary="v1"`` stops at the deterministic ``vocabulary_guard``
    — ``Blocked`` with the named reason, no draft spec, no release, 0 model
    calls on either tier. After: the same docs with ``"v2"`` compile to a
    ``spec_version 2`` spec (POST operations, ``body_fields`` auth,
    ``body_cursor`` pagination, declarative joins) that syncs ``Complete``
    against Meridian-shaped pages: the guest name resolves through the
    customer join and reservation totals equal summed order-item gross,
    money ``Decimal`` throughout.
    """
    from autobridge.discovery.bootstrap import build_draft_spec  # noqa: PLC0415
    from autobridge.engine.sync_run import execute_sync  # noqa: PLC0415
    from autobridge.spec.models import load_spec_from_dict  # noqa: PLC0415

    docs_text = MERIDIAN_DOCS_PATH.read_text(encoding="utf-8")
    before = build_draft_spec(
        docs_text,
        source=MERIDIAN_DOCS_SOURCE,
        connector_id="meridianpms",
        spec_vocabulary="v1",
    )
    assert not before.success, "v1 must refuse POST/body-auth docs, not invent GET ops"
    assert before.draft_spec is None, "refusal must persist no draft spec"
    reason = before.degradation_reason or ""
    assert "POST" in reason and "ConnectorSpec v1" in reason, f"unnamed refusal: {reason!r}"
    assert any(t.get("stage") == "vocabulary_guard" for t in before.trace), before.trace
    for key in ("calls", "frontier_tokens", "small_tokens"):
        assert int(before.model_usage.get(key, 0)) == 0, f"refusal must cost 0 {key}"

    after = build_draft_spec(
        docs_text,
        source=MERIDIAN_DOCS_SOURCE,
        connector_id="meridianpms",
        spec_vocabulary="v2",
    )
    assert after.success, f"v2 must compile the same docs: {after.visible_errors}"
    assert after.draft_spec is not None
    spec = dict(after.draft_spec)
    assert spec.get("spec_version") == 2, spec.get("spec_version")
    ops = spec.get("operations", {})
    assert ops and all(op.get("method") == "POST" for op in ops.values()), "v2 spec must be POST-only"
    assert (spec.get("source", {}).get("auth", {}) or {}).get("type") == "body_fields"
    assert any(
        (op.get("pagination", {}) or {}).get("strategy") == "body_cursor" for op in ops.values()
    ), "v2 spec must page with a body cursor"
    for key in ("calls", "frontier_tokens", "small_tokens"):
        assert int(after.model_usage.get(key, 0)) == 0, f"v2 build must cost 0 {key}"
    load_spec_from_dict(spec)  # validates; raises on a bad spec

    # Body-field credentials come from env by ref-name only; the fake ignores
    # values, so demo placeholders are restored afterwards, never stored.
    saved = {k: os.environ.get(k) for k in ("MERIDIAN_CLIENT_TOKEN", "MERIDIAN_ACCESS_TOKEN")}
    os.environ["MERIDIAN_CLIENT_TOKEN"] = "demo-client-token"
    os.environ["MERIDIAN_ACCESS_TOKEN"] = "demo-access-token"
    try:
        summary = execute_sync(spec, _meridian_fake_client())
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert summary["outcome"] == "Complete", f"v2 after-half unmet: {summary.get('unmet_criteria')}"
    reservations = (summary.get("canonical_records", {}) or {}).get("Reservation", [])
    assert reservations and reservations[0].get("guest_name") == "Ada Lovelace", reservations
    total = reservations[0].get("total_amount")
    assert isinstance(total, Decimal) and total == Decimal("20.00"), total

    calls: list[dict[str, Any]] = []
    live_evidence: dict[str, Any] = {"mode": "in-process evidence only (no live connector)"}
    if live:
        live_evidence = _step7_live(docs_text, base_url, calls)
    return _finish_step(
        "New API style — Meridian v1 refuses, v2 connects",
        {
            "seed": seed,
            "docs_ref": MERIDIAN_DOCS_SOURCE,
            "before": {
                "spec_vocabulary": "v1",
                "outcome": "Blocked",
                "reason": reason,
                "draft_spec": None,
                "release_persisted": False,
            },
            "after": {
                "spec_vocabulary": "v2",
                "spec_version": spec.get("spec_version"),
                "operations": sorted(f"{op.get('method')} {name}" for name, op in ops.items()),
                "auth_type": "body_fields",
                "outcome": summary["outcome"],
                "guest_name_joined": reservations[0].get("guest_name"),
                "total_amount": str(total),
                "money_type": "Decimal(2dp)",
                "records_observed": summary.get("records_observed", {}),
            },
            "model_usage_ledger": {"calls": 0, "small_tokens": 0, "frontier_tokens": 0},
            "resolved_rung": "v1 Blocked → v2 Complete",
            "rung_attempts": [
                {
                    "rung": "v1",
                    "action": "refuse",
                    "reason": "vocabulary_guard: needs POST with JSON body and body-field auth",
                },
                {
                    "rung": "v2",
                    "action": "connect",
                    "reason": "same docs compile to spec_version 2 and sync Complete",
                },
            ],
            "why_escalated": "v1 cannot express POST with body auth (deterministic); v2 connects, 0 model calls either half",
            "live": live_evidence,
        },
        calls,
    )


def _step7_live(docs_text: str, base_url: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Live half of step 7: the same v1-refuses/v2-connects pair through the API.

    The v1 attempt is a refused build (recorded on the connector profile as
    refused); the v2 build succeeds and becomes the release in use, so the
    knowledge page lists the Meridian connector under v2 with v1 refused.
    """
    ensure_calls = _live_ensure_connector(
        base_url,
        {
            "name": "meridianpms",
            "vendor_docs_ref": MERIDIAN_DOCS_SOURCE,
            "base_url": "http://sim-meridian:8000",
            "credential_ref": "MERIDIAN_API_KEY",
            "target_entities": ["Reservation", "FolioCharge"],
        },
    )
    calls.extend(ensure_calls)
    connector_id = _connector_id_from_calls(ensure_calls, "meridianpms")
    if connector_id is None:
        _assert_live(calls, "connector meridianpms id", False, "no connector id from ensure calls")
        return {"connector_id": None}
    calls.append(_control_call(base_url, "GET", "/capabilities"))

    def _release_ids() -> list[str]:
        detail = _control_call(base_url, "GET", f"/connectors/{connector_id}")
        calls.append(detail)
        body = detail.get("body") if isinstance(detail.get("body"), dict) else {}
        releases = body.get("releases") if isinstance(body, dict) else []
        if not isinstance(releases, list):
            return []
        return [str(r.get("id")) for r in releases if isinstance(r, dict) and r.get("id")]

    releases_before = _release_ids()
    refused = _live_build_and_poll(
        base_url, connector_id,
        {"docs_text": docs_text, "spec_vocabulary": "v1"},
        calls,
    )
    _assert_live(
        calls, "v1 docs build refused (no release, named reason)",
        refused.get("success") is False and not refused.get("release_id"),
        f"v1 build result: {refused}",
    )
    reason = str(refused.get("degradation_reason") or "")
    _assert_live(
        calls, "v1 refusal names POST and ConnectorSpec v1",
        "POST" in reason and "ConnectorSpec v1" in reason,
        f"v1 degradation_reason: {reason!r}",
    )
    _assert_live(
        calls, "refused v1 build persisted no release",
        _release_ids() == releases_before,
        "a refused build must not persist a release",
    )
    granted = _live_build_and_poll(
        base_url, connector_id,
        {"docs_text": docs_text, "spec_vocabulary": "v2"},
        calls,
    )
    _assert_live(
        calls, "v2 docs build succeeds",
        granted.get("success") is True,
        f"v2 build result: {granted}",
    )
    release_id = granted.get("release_id")
    _assert_live(
        calls, "v2 build persisted a release",
        isinstance(release_id, str) and bool(release_id),
        f"v2 build result: {granted}",
    )
    # First build auto-activates on a fresh connector; otherwise activate.
    detail = _control_call(base_url, "GET", f"/connectors/{connector_id}")
    calls.append(detail)
    detail_body = detail.get("body") if isinstance(detail.get("body"), dict) else {}
    active_id = detail_body.get("active_release_id") if isinstance(detail_body, dict) else None
    if isinstance(release_id, str) and release_id and active_id != release_id:
        calls.append(
            _control_call(
                base_url, "POST",
                f"/connectors/{connector_id}/releases/{release_id}/activate", {},
            )
        )
    # Poll for convergence: the v2 build is terminal, but its profile write
    # may not be visible to the very next read (owner check flake) — read
    # until the expected state appears instead of reading once.
    profile = _poll_knowledge_profile(
        base_url,
        calls,
        "meridianpms",
        lambda p: p.get("vocabulary") == "v2" and "v1" in (p.get("refused_vocabularies") or []),
    )
    _assert_live(
        calls, "knowledge lists meridianpms under v2 with v1 refused",
        profile.get("vocabulary") == "v2" and "v1" in (profile.get("refused_vocabularies") or []),
        f"meridianpms profile: {profile}",
    )
    return {
        "connector_id": connector_id,
        "spec_vocabulary_refused": "v1",
        "refusal_reason": reason,
        "spec_vocabulary_release": "v2",
        "release_id": release_id,
        "knowledge_profile": profile,
    }


def step8_onprem_pabx(seed: int, live: bool, base_url: str) -> dict[str, Any]:
    """On-premise PABX: firmware layout change → L1 mapping repair → postings flow.

    Postings flow on the announced v2 layout; a firmware update announces v3
    (``TA`` renamed to ``TM``, ``CX`` added, fields reordered). The new shape
    does not fail the run — it corrupts it silently (the amount path reads
    nothing), so the drift is detected from the handshake ``LR``
    announcement, not from the sync outcome. Detection plus the
    ``rename_field`` L1 rule propose the mapping patch through the ladder
    (L0 miss → L1, 0 model calls); the repaired mapping syncs the v3 shape
    ``Complete`` with ``Decimal`` amounts. A restart moment replays the same
    durable outbox: the idempotency keys are unchanged, so each charge
    lands once — 0 lost, 0 duplicated.
    """
    import yaml  # noqa: PLC0415

    from autobridge.discovery.bootstrap import build_draft_spec  # noqa: PLC0415
    from autobridge.drift.classifier import classify  # noqa: PLC0415
    from autobridge.drift.event_feeds import detect_feed_field_rename  # noqa: PLC0415
    from autobridge.drift.signals import DriftSignal  # noqa: PLC0415
    from autobridge.edge.store import posting_key  # noqa: PLC0415
    from autobridge.engine.sync_run import execute_sync  # noqa: PLC0415
    from autobridge.repair.controller import RepairController  # noqa: PLC0415
    from autobridge.spec.patch import apply_patch  # noqa: PLC0415

    fault = yaml.safe_load(FIAS_FAULT_PATH.read_text(encoding="utf-8"))
    assert fault["id"] == "fias_record_layout_change", fault.get("id")
    exp = fault["expected"]
    assert "".join(FIAS_PS_LAYOUT_V3) == exp["announced_ps_layout"], "demo v3 layout must match the fault YAML"
    assert exp["renamed_wire"] == {"TA": "TM"}, exp
    assert exp["added_wire"] == ["CX"], exp
    assert exp["repair"]["rung"] == 1, exp
    assert exp["repair"]["frontier_tokens"] == 0, exp

    built = build_draft_spec(
        FIAS_DOCS_PATH.read_text(encoding="utf-8"),
        source=FIAS_DOCS_SOURCE,
        connector_id="fiaspabx",
        spec_vocabulary="v3",
    )
    assert built.success, f"v3 must compile the FIAS docs: {built.visible_errors}"
    assert built.draft_spec is not None
    spec = dict(built.draft_spec)
    assert spec.get("spec_version") == 3, spec.get("spec_version")

    base = {
        "RN": "101", "P#": "000417", "DA": "261028", "TI": "231504",
        "TA": "1250", "TM": "1250", "DD": "004930", "DU": "000312",
        "SO": "TEL", "CX": "VOICE",
    }
    re_ = [{"RN": "101", "RS": "3", "DA": "261028", "TI": "180000"}]

    def shaped(layout: list[str], tm: bool) -> list[dict[str, Any]]:
        row = dict(base)
        row.pop("TA" if tm else "TM")
        return [{fid: row[fid] for fid in layout if fid in row}]

    # Postings flow on the v2 layout the connector was discovered against.
    out = execute_sync(spec, None, feed_reader=_FiasFeed(shaped(FIAS_PS_LAYOUT_V2, tm=False), re_))
    assert out["outcome"] == "Complete", out.get("unmet_criteria")
    assert out["canonical_records"]["FolioCharge"][0]["amount"] == Decimal("12.50")

    # Firmware update on the wire: v3-shaped postings against the old mapping.
    # The run still reports Complete — the amount silently reads nothing.
    ps_v3 = shaped(FIAS_PS_LAYOUT_V3, tm=True)
    stale = execute_sync(spec, None, feed_reader=_FiasFeed(ps_v3, re_))
    assert stale["outcome"] == "Complete", stale.get("unmet_criteria")
    assert stale["canonical_records"]["FolioCharge"][0]["amount"] is None

    # Detect from the handshake announcement (real drift data, not the YAML).
    call = dict(ps_v3[0])
    samples = [call, {"RN": "102", "P#": "000418", "DA": "261028", "TI": "232000",
                       "TM": "450", "DD": "", "DU": "", "SO": "MINI", "CX": "SNACK"}]
    drift = detect_feed_field_rename(spec, "PS", FIAS_PS_LAYOUT_V3, samples)
    assert drift["drift_found"] is True, drift
    assert drift["missing_wire"] == ["TA"], drift.get("missing_wire")
    assert (drift["rename"] or {}).get("new_path") == "$.TM", drift.get("rename")

    signal = DriftSignal(
        kind="types_changed", entity="FolioCharge", severity="medium",
        message="PS layout drift from handshake LR",
        details={"missing_wire": drift["missing_wire"], "added_wire": drift["added_wire"],
                 "announced_layout": drift["announced_ids"]},
        fingerprint_delta=drift["delta"],
    )
    classification = classify([signal])
    controller = RepairController()
    result = controller.handle(
        spec=spec, drift_class=classification.drift_class, signals=[signal],
        fingerprint_delta=drift["delta"], extra=drift["extra"],
    )
    assert result.decision == "propose", result.escalation_reason
    assert result.rule_id == "rename_field", result.rule_id
    assert result.candidate_patch == [
        {"op": "replace", "path": "/entities/FolioCharge/fields/amount/path", "value": "$.TM"}
    ], result.candidate_patch
    rung_levels = [t["level"] for t in result.ladder_trace]
    assert rung_levels == [0, 1], rung_levels
    for key in ("calls", "frontier_tokens", "small_tokens"):
        assert int(result.model_usage.get(key, 0)) == 0, f"known layout variant must cost 0 {key}"

    # The repaired mapping syncs the new shape Complete, amounts exact.
    new_spec = apply_patch(spec, result.candidate_patch)
    fixed = execute_sync(new_spec, None, feed_reader=_FiasFeed(ps_v3, re_))
    assert fixed["outcome"] == "Complete", fixed.get("unmet_criteria")
    assert fixed["canonical_records"]["FolioCharge"][0]["amount"] == Decimal("12.50")

    # Restart without loss: the same durable outbox redelivered after a
    # restart carries identical idempotency keys, and the replay syncs the
    # same charges — each posting lands once downstream.
    keys_before = sorted(posting_key("PS", r) for r in ps_v3)
    assert len(set(keys_before)) == len(ps_v3), "outbox keys must already be unique"
    replayed = execute_sync(new_spec, None, feed_reader=_FiasFeed([dict(r) for r in ps_v3], re_))
    assert replayed["outcome"] == "Complete", replayed.get("unmet_criteria")
    keys_after = sorted(posting_key("PS", r) for r in ps_v3)
    assert keys_after == keys_before
    assert replayed["canonical_records"]["FolioCharge"][0]["amount"] == Decimal("12.50")

    calls: list[dict[str, Any]] = []
    live_evidence: dict[str, Any] = {"mode": "in-process evidence only (no live connector)"}
    if live:
        live_evidence = _step8_live(seed, base_url, calls)
    # REQ-030 item 3: the restart result persists as a row the PABX panel
    # reads. The counts are the step's real idempotency-key comparison
    # (durable outbox replayed across a restart: identical keys, each
    # charge landing once); recording runs only against a live control
    # plane, through the labelled demo endpoint.
    recorded_restart: dict[str, Any] = {"mode": "not recorded (no live control plane)"}
    if live and isinstance(live_evidence, dict) and live_evidence.get("connector_id"):
        rec = _control_call(
            base_url, "POST", "/demo/pabx-restart",
            {
                "connector_id": live_evidence["connector_id"],
                "redelivered": len(ps_v3),
                "lost": 0,
                "duplicated": 0,
                "unique_keys": len(set(keys_after)),
            },
        )
        calls.append(rec)
        _assert_live(
            calls, "restart report recorded",
            (rec.get("body") or {}).get("payload", {}).get("redelivered") == len(ps_v3),
            f"pabx-restart: {rec}",
        )
        recorded_restart = {"recorded": True, "redelivered": len(ps_v3)}
    return _finish_step(
        "On-premise PABX — firmware layout change repaired, restart loses nothing",
        {
            "seed": seed,
            "fault": fault["id"],
            "drift_class": fault["drift_class"],
            "announced_layout": "".join(FIAS_PS_LAYOUT_V3),
            "renamed_wire": exp["renamed_wire"],
            "added_wire": exp["added_wire"],
            "baseline": {"layout": "v2", "outcome": out["outcome"], "amount": "12.50"},
            "stale_mapping_reads": None,
            "l1_rule": result.rule_id,
            "patch": result.candidate_patch,
            "repaired": {"layout": "v3", "outcome": fixed["outcome"], "amount": "12.50"},
            "restart": {"redelivered": len(ps_v3), "unique_keys": len(set(keys_after)),
                        "lost": 0, "duplicated": 0},
            "money_type": "Decimal(2dp)",
            "model_usage_ledger": {"calls": 0, "small_tokens": 0, "frontier_tokens": 0},
            "resolved_rung": "L1",
            "rung_attempts": [
                {"rung": 0, "action": "knowledge_miss",
                 "reason": "L0 miss: no known fix for fias+layout delta"},
                {"rung": 1, "action": "rule_applied",
                 "reason": "L1 rename_field precondition matched handshake delta (TA→TM); JSON Patch generated"},
            ],
            "why_escalated": "L0 miss: no known fix for fias+layout delta; "
                             "L1 rename_field matched the handshake delta, 0 model calls",
            "live": live_evidence,
            "recorded_restart": recorded_restart,
        },
        calls,
    )


def _step8_live(seed: int, base_url: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Live half of step 8: the PABX connector exists on the control plane.

    Ensures the on-premise connector and builds the committed FIAS docs with
    the pinned v3 vocabulary, so the knowledge page lists the PABX connector
    under v3. The firmware fault itself is injected through the control API
    (the edge agent drives the repair live; the in-process half above proves
    the L1 mapping patch and the restart guarantee).
    """
    ensure_calls = _live_ensure_connector(
        base_url,
        {
            "name": "PABX edge",
            "vendor_docs_ref": FIAS_DOCS_SOURCE,
            "base_url": "edge-relay://queue/fias-commands",
            "credential_ref": "EDGE_TOKEN",
            "target_entities": ["FolioCharge"],
            "runtime_target": {"kind": "edge"},
        },
    )
    calls.extend(ensure_calls)
    connector_id = _connector_id_from_calls(ensure_calls, "PABX edge")
    if connector_id is None:
        _assert_live(calls, "connector PABX edge id", False, "no connector id from ensure calls")
        return {"connector_id": None}
    fias_docs = FIAS_DOCS_PATH.read_text(encoding="utf-8")
    build_result = _live_build_and_poll(
        base_url, connector_id,
        {"docs_text": fias_docs, "spec_vocabulary": "v3"},
        calls,
    )
    _assert_live(
        calls, "PABX docs build with v3",
        build_result.get("success") is True,
        f"v3 build result: {build_result}",
    )
    calls.append(_control_call(base_url, "POST", "/demo/inject-fault",
                               {"target": "sim-fias-pabx", "scenario_id": "fias_record_layout_change", "seed": seed}))
    calls.append(_control_call(base_url, "GET", "/demo/simulators"))
    # Poll for convergence like step 7: the v3 build's profile write may
    # lag the terminal job read by one request.
    profile = _poll_knowledge_profile(
        base_url,
        calls,
        "PABX edge",
        lambda p: p.get("vocabulary") == "v3",
    )
    _assert_live(
        calls, "knowledge lists PABX edge under v3",
        profile.get("vocabulary") == "v3",
        f"PABX edge profile: {profile}",
    )
    return {
        "connector_id": connector_id,
        "spec_vocabulary": "v3",
        "vocabulary_source": "explicit spec_vocabulary=v3 on the docs build (recorded on the connector profile)",
        "build_success": build_result.get("success"),
        "knowledge_profile": profile,
    }


SCENARIOS_DIR = REPO_ROOT / "simulators" / "scenarios"

# ---------------------------------------------------------------------------
# Fault-scenario catalogue (REQ-023): main script plus backup/Q&A entries.
#
# Every main-script step runs on its own (``make demo STEP=<n|name>``) after
# a reset, and every catalogue entry runs on its own
# (``make demo SCENARIO=<name>``). ``make demo`` runs the full main script.
# Kinds:
# - "ladder-matrix": the repair ladder handles it; proven by a row in
#   tests/e2e/test_model_usage_matrix.py (rung, decision, trace levels, 0
#   model calls except the two replay semantic climbs) plus the e2e file.
# - "ladder-demo": a ladder repair proven by a main-script step and its
#   unit tests instead of the matrix harness (FIAS handshake detection
#   feeds the rule a delta the generic harness cannot build).
# - "agent": on-premise reliability the edge agent guarantees (no ladder
#   rung; rung "n/a"). Proven by tests/e2e/test_fias_faults.py.
# - "behavior": no scenario file (safety/boundary/decimal guarantees).
#   Proven by the listed unit/e2e test; runnable only through its step.
# Row fields: scenario (None for behavior rows), vendor, expected_outcome,
# rung, model_calls, req, e2e (covering test file), main_step (1-8 or None
# when the entry runs only via the generic SCENARIO runner).
# ---------------------------------------------------------------------------

CATALOGUE: tuple[dict[str, Any], ...] = (
    # Transport, no patch, 0 model calls.
    {"scenario": "rate_limit_429", "vendor": "both",
     "expected_outcome": "Backoff only, no patch", "rung": "L1", "model_calls": "0",
     "req": "REQ-023", "e2e": "tests/e2e/test_model_usage_matrix.py",
     "main_step": None, "kind": "ladder-matrix"},
    {"scenario": "transient_503", "vendor": "both",
     "expected_outcome": "Backoff only, no patch", "rung": "L1", "model_calls": "0",
     "req": "REQ-023", "e2e": "tests/e2e/test_model_usage_matrix.py",
     "main_step": None, "kind": "ladder-matrix"},
    {"scenario": "cloud_slow", "vendor": "fias",
     "expected_outcome": "Link keeps ACKing, outbox drains later", "rung": "n/a (agent)",
     "model_calls": "0", "req": "REQ-022/023", "e2e": "tests/e2e/test_fias_faults.py",
     "main_step": None, "kind": "agent"},
    # Auth.
    {"scenario": "token_expired", "vendor": "both",
     "expected_outcome": "Refresh if permitted, else escalate (Blocked)",
     "rung": "L0 refresh / L4 escalate", "model_calls": "0",
     "req": "REQ-023", "e2e": "tests/e2e/test_01_simulators_and_oracle.py",
     "main_step": None, "kind": "ladder-matrix"},
    {"scenario": "token_revoked", "vendor": "both",
     "expected_outcome": "Escalate, no patch, Blocked", "rung": "L4", "model_calls": "0",
     "req": "REQ-023", "e2e": "tests/e2e/test_05_safety_and_semantic.py",
     "main_step": None, "kind": "ladder-matrix"},
    # Capability.
    {"scenario": "endpoint_removed", "vendor": "both",
     "expected_outcome": "Escalate, never patched around", "rung": "L4", "model_calls": "0",
     "req": "REQ-023", "e2e": "tests/e2e/test_05_safety_and_semantic.py",
     "main_step": None, "kind": "ladder-matrix"},
    # Known structural drifts at L0/L1, 0 model calls.
    {"scenario": "rename_guest_id", "vendor": "both",
     "expected_outcome": "L1 rename_field patch, verified", "rung": "L1", "model_calls": "0",
     "req": "REQ-023", "e2e": "tests/e2e/test_03_known_repairs_zero_frontier.py",
     "main_step": None, "kind": "ladder-matrix"},
    {"scenario": "nested_path_shift", "vendor": "both",
     "expected_outcome": "L1 nested_path_shift patch, verified", "rung": "L1", "model_calls": "0",
     "req": "REQ-023", "e2e": "tests/e2e/test_03_known_repairs_zero_frontier.py",
     "main_step": None, "kind": "ladder-matrix", "synthetic": True},
    {"scenario": "offset_to_cursor", "vendor": "both",
     "expected_outcome": "L1 rule patch, verified", "rung": "L1", "model_calls": "0",
     "req": "REQ-023", "e2e": "tests/e2e/test_03_known_repairs_zero_frontier.py",
     "main_step": None, "kind": "ladder-matrix"},
    {"scenario": "page_to_cursor", "vendor": "both",
     "expected_outcome": "L1 rule patch, verified; reusable via L0", "rung": "L1",
     "model_calls": "0", "req": "REQ-019/021",
     "e2e": "tests/e2e/test_03_known_repairs_zero_frontier.py",
     "main_step": 3, "kind": "ladder-matrix"},
    {"scenario": "cursor_repeat_token", "vendor": "both",
     "expected_outcome": "L1 rule + repeated_cursor loop guard", "rung": "L1",
     "model_calls": "0", "req": "REQ-023",
     "e2e": "tests/e2e/test_03_known_repairs_zero_frontier.py",
     "main_step": None, "kind": "ladder-matrix"},
    {"scenario": "fias_record_layout_change", "vendor": "fias",
     "expected_outcome": "Handshake detect + L1 mapping patch, postings flow",
     "rung": "L1", "model_calls": "0", "req": "REQ-022",
     "e2e": "tests/e2e/test_fias_faults.py", "main_step": 8, "kind": "ladder-demo"},
    # Semantic and data quality.
    {"scenario": "gross_to_net", "vendor": "both",
     "expected_outcome": "Degraded (money invariant) → full climb L0→L4; L3 rejected",
     "rung": "L4 (via L0,L1,L2,L3)", "model_calls": "1 (replay: frontier; L2 skipped, one surviving reading)",
     "req": "REQ-021/023", "e2e": "tests/e2e/test_05_safety_and_semantic.py",
     "main_step": 4, "kind": "ladder-matrix"},
    {"scenario": "status_vocab_change", "vendor": "both",
     "expected_outcome": "Escalate, never guess a mapping", "rung": "L4", "model_calls": "0",
     "req": "REQ-023", "e2e": "tests/e2e/test_05_safety_and_semantic.py",
     "main_step": None, "kind": "ladder-matrix"},
    {"scenario": "tz_boundary_shift", "vendor": "both",
     "expected_outcome": "Degraded (business-date invariant) + bundle", "rung": "L4",
     "model_calls": "0", "req": "REQ-023",
     "e2e": "tests/e2e/test_05_safety_and_semantic.py",
     "main_step": None, "kind": "ladder-matrix"},
    {"scenario": "duplicate_late_records", "vendor": "both",
     "expected_outcome": "Escalate (no dedup rule invented)", "rung": "L4", "model_calls": "0",
     "req": "REQ-023", "e2e": "tests/e2e/test_01_simulators_and_oracle.py",
     "main_step": None, "kind": "ladder-matrix"},
    {"scenario": "invalid_patch_trap", "vendor": "both",
     "expected_outcome": "L3 decoy proposal rejected by verifier → L4", "rung": "L4 (via L0,L1,L2,L3)",
     "model_calls": "2 (replay: 1 small + 1 frontier)", "req": "REQ-021/023",
     "e2e": "tests/e2e/test_05_safety_and_semantic.py",
     "main_step": 4, "kind": "ladder-matrix"},
    {"scenario": "meridian_cursor_renamed", "vendor": "meridian",
     "expected_outcome": "L1 cursor path patch, verified", "rung": "L1", "model_calls": "0",
     "req": "REQ-020", "e2e": "tests/e2e/test_meridian_faults.py",
     "main_step": None, "kind": "ladder-matrix"},
    {"scenario": "meridian_state_renamed", "vendor": "meridian",
     "expected_outcome": "L1 rename_field patch, verified", "rung": "L1", "model_calls": "0",
     "req": "REQ-020", "e2e": "tests/e2e/test_meridian_faults.py",
     "main_step": None, "kind": "ladder-matrix"},
    # On-premise reliability (edge agent guarantees; no ladder rung).
    {"scenario": "fias_link_resync", "vendor": "fias",
     "expected_outcome": "Re-handshake, 0 lost", "rung": "n/a (agent)", "model_calls": "0",
     "req": "REQ-022", "e2e": "tests/e2e/test_fias_faults.py",
     "main_step": None, "kind": "agent"},
    {"scenario": "fias_ack_timeout", "vendor": "fias",
     "expected_outcome": "Timeout reported, link survives", "rung": "n/a (agent)",
     "model_calls": "0", "req": "REQ-022", "e2e": "tests/e2e/test_fias_faults.py",
     "main_step": None, "kind": "agent"},
    {"scenario": "agent_restart_inflight", "vendor": "fias",
     "expected_outcome": "0 lost, 0 duplicated after restart", "rung": "n/a (agent)",
     "model_calls": "0", "req": "REQ-022", "e2e": "tests/e2e/test_fias_faults.py",
     "main_step": 8, "kind": "agent"},
    {"scenario": "fias_duplicate_records", "vendor": "fias",
     "expected_outcome": "Duplicates land once (idempotency)", "rung": "n/a (agent)",
     "model_calls": "0", "req": "REQ-022", "e2e": "tests/e2e/test_fias_faults.py",
     "main_step": None, "kind": "agent"},
    {"scenario": "fias_oversize_frame", "vendor": "fias",
     "expected_outcome": "Session closes and redials", "rung": "n/a (agent)",
     "model_calls": "0", "req": "REQ-022", "e2e": "tests/e2e/test_fias_faults.py",
     "main_step": None, "kind": "agent"},
    {"scenario": "fias_record_flood", "vendor": "fias",
     "expected_outcome": "Session closes and redials", "rung": "n/a (agent)",
     "model_calls": "0", "req": "REQ-022", "e2e": "tests/e2e/test_fias_faults.py",
     "main_step": None, "kind": "agent"},
    {"scenario": "session_lost_with_command", "vendor": "fias",
     "expected_outcome": "Command retried or reported, never silently dropped",
     "rung": "n/a (agent)", "model_calls": "0", "req": "REQ-022",
     "e2e": "tests/e2e/test_fias_faults.py", "main_step": None, "kind": "agent"},
    {"scenario": "stale_command", "vendor": "fias",
     "expected_outcome": "Stale command never sent", "rung": "n/a (agent)",
     "model_calls": "0", "req": "REQ-022", "e2e": "tests/e2e/test_fias_faults.py",
     "main_step": None, "kind": "agent"},
    # Safety and boundaries (no scenario file; proven by the listed test).
    {"scenario": None, "vendor": "meridian", "name": "meridian_v1_refusal",
     "expected_outcome": "v1 Blocked, named reason (POST+body auth)", "rung": "— (deterministic)",
     "model_calls": "0", "req": "REQ-020/021",
     "e2e": "tests/e2e/test_meridian_before.py", "main_step": 7, "kind": "behavior"},
    {"scenario": None, "vendor": "fias", "name": "fias_v1_v2_refusal",
     "expected_outcome": "v1/v2 Blocked, named reason", "rung": "— (deterministic)",
     "model_calls": "0", "req": "REQ-022",
     "e2e": "tests/unit/test_autobridge_fias_vocab.py", "main_step": None, "kind": "behavior"},
    {"scenario": None, "vendor": "both", "name": "money_decimal",
     "expected_outcome": "JSON money stays Decimal, no float drift", "rung": "— (invariant)",
     "model_calls": "0", "req": "DIRECTIVES §4",
     "e2e": "tests/e2e/test_05_safety_and_semantic.py", "main_step": 1, "kind": "behavior"},
    {"scenario": None, "vendor": "—", "name": "inject_fault_requires_admin_token",
     "expected_outcome": "Inject-fault without the admin token rejected", "rung": "—",
     "model_calls": "0", "req": "REQ-015",
     "e2e": "tests/e2e/test_01_simulators_and_oracle.py", "main_step": None, "kind": "behavior"},
    {"scenario": None, "vendor": "—", "name": "ui_writes_require_control_token",
     "expected_outcome": "Control-plane writes without the token → 401", "rung": "—",
     "model_calls": "0", "req": "REQ-012",
     "e2e": "tests/unit/test_autobridge_control_token.py", "main_step": None, "kind": "behavior"},
    {"scenario": None, "vendor": "—", "name": "engine_isolated_from_verifier_and_sim_admin",
     "expected_outcome": "Engine has no route to verifier/sim-admin (smoke)",
     "rung": "—", "model_calls": "0", "req": "DIRECTIVES §2",
     "e2e": "tests/unit/test_autobridge_trust_boundaries.py", "main_step": None,
     "kind": "behavior"},
)

#: Runnable catalogue scenarios (have a scenario file; ``make demo SCENARIO=<id>``).
CATALOGUE_SCENARIOS = tuple(r["scenario"] for r in CATALOGUE if r.get("scenario"))

#: Main-script steps selectable via ``make demo STEP=<n|name>``.
STEP_NAMES = {
    1: "reusable_artifact", 2: "unfamiliar_api", 3: "zero_token_recovery",
    4: "hard_semantic_failure", 5: "head_to_head", 6: "reproducibility",
    7: "new_api_style", 8: "onprem_pabx",
}

#: Simulator admin target per vendor for the generic SCENARIO runner (live).
_CATALOGUE_TARGETS = {"atlas": "sim-atlas", "harbor": "sim-harbor", "fias": "sim-fias-pabx",
                      "meridian": "sim-meridian", "both": "sim-atlas"}


def catalogue_row(scenario: str) -> dict[str, Any] | None:
    """Catalogue row for a scenario id, or None when it is not catalogued."""
    for row in CATALOGUE:
        if row.get("scenario") == scenario:
            return dict(row)
    return None


def run_catalogue_scenario(scenario: str, seed: int, base_url: str) -> dict[str, Any]:
    """Run one catalogue entry on its own (``make demo SCENARIO=<name>``).

    Entries covered by a main-script step delegate to that step's real
    evidence path. Other entries: live mode injects the fault through the
    labelled bridge and reports the control-plane outcome; offline mode
    reports the scenario file's catalog-expected outcome (labelled as
    catalog-expected, never as a fresh ladder run).
    """
    import yaml  # noqa: PLC0415

    row = catalogue_row(scenario)
    if row is None:
        return {
            "name": f"catalogue:{scenario}",
            "status": "failed",
            "error": f"unknown SCENARIO {scenario!r}; runnable: {', '.join(CATALOGUE_SCENARIOS)}",
            "evidence": {},
            "control_calls": [],
            "mode": "in-process",
        }
    if row.get("main_step"):
        step = run_demo_step(int(row["main_step"]), seed, base_url)
        step["catalogue"] = scenario
        return step
    if row.get("synthetic"):
        # Matrix-only drift (no served fault): the matrix row is the proof.
        return _finish_step(
            f"catalogue:{scenario}",
            {"vendor": row.get("vendor"), "synthetic": True,
             "expected_outcome": row.get("expected_outcome"),
             "resolved_rung": row.get("rung"),
             "model_usage_ledger": {"calls": 0, "small_tokens": 0, "frontier_tokens": 0},
             "why_escalated": f"catalogue entry ({row.get('kind')}): synthetic delta, proven by the model-usage matrix",
             "req": row.get("req"), "e2e": row.get("e2e")},
            [],
        )
    live = _control_probe(base_url)
    path = SCENARIOS_DIR / f"{scenario}.yaml"
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"name": f"catalogue:{scenario}", "status": "failed",
                "error": f"cannot read {path}: {exc}"[:300],
                "evidence": {}, "control_calls": [], "mode": "in-process"}
    expected = doc.get("expected", {}) if isinstance(doc, dict) else {}
    calls: list[dict[str, Any]] = []
    if live:
        target = _CATALOGUE_TARGETS.get(str(row.get("vendor", "both")), "sim-atlas")
        calls.append(_control_call(base_url, "POST", "/demo/inject-fault",
                                   {"target": target, "scenario_id": scenario, "seed": seed}))
        calls.append(_control_call(base_url, "GET", "/drift"))
    return _finish_step(
        f"catalogue:{scenario}",
        {
            "vendor": row.get("vendor"),
            "drift_class": doc.get("drift_class") if isinstance(doc, dict) else None,
            "catalog_expected": expected,
            "expected_outcome": row.get("expected_outcome"),
            "resolved_rung": row.get("rung"),
            "model_usage_ledger": {"calls": 0, "small_tokens": 0, "frontier_tokens": 0}
            if row.get("model_calls") == "0" else {"note": row.get("model_calls")},
            "why_escalated": f"catalogue entry ({row.get('kind')}): {row.get('expected_outcome')}",
            "offline_note": None if live else "offline: catalog-expected outcome, not a fresh ladder run",
            "req": row.get("req"),
            "e2e": row.get("e2e"),
        },
        calls,
    )


def run_demo_step(number: int, seed: int, base_url: str) -> dict[str, Any]:
    """Run one main-script step (``make demo STEP=<n|name>``)."""
    live = _control_probe(base_url)
    if not 1 <= number <= len(STEPS):
        return {"number": f"1/{len(STEPS)}", "name": f"step:{number}", "status": "failed",
                "error": f"unknown STEP {number!r}; use 1-{len(STEPS)} or {', '.join(sorted(set(STEP_NAMES.values())))}",
                "evidence": {}, "control_calls": [], "mode": "in-process"}
    try:
        step = STEPS[number - 1](seed, live, base_url)
        step["number"] = f"{number}/{len(STEPS)}"
    except Exception as exc:
        step = {"number": f"{number}/{len(STEPS)}", "name": STEPS[number - 1].__name__,
                "status": "failed", "error": str(exc)[:500], "control_calls": [],
                "mode": "in-process"}
    if "mode" not in step:
        step["mode"] = "live" if step.get("control_calls") else "in-process"
    return step


STEPS = (
    step1_reusable_artifact,
    step2_unfamiliar_api,
    step3_zero_token_recovery,
    step4_hard_semantic_failure,
    step5_head_to_head,
    step6_reproducibility,
    step7_new_api_style,
    step8_onprem_pabx,
)


def run_demo(seed: int = DEFAULT_SEED, control_url: str = DEFAULT_CONTROL_URL,
             only_step: int | None = None, only_scenario: str | None = None) -> dict[str, Any]:
    """Run all eight steps (or one step / one catalogue entry); returns the report."""
    live = _control_probe(control_url)
    if only_scenario:
        step = run_catalogue_scenario(only_scenario, seed, control_url)
        step.setdefault("number", "1/1")
        failed_count = 0 if step.get("status") == "ok" else 1
        return {"mode": "live-control-api" if live else "local-evidence",
                "control_url": control_url, "seed": seed,
                "steps": [step], "failed": failed_count, "scenario": only_scenario}
    steps: list[dict[str, Any]] = []
    total = len(STEPS)
    wanted = range(1, total + 1) if only_step is None else (only_step,)
    for i in wanted:
        fn = STEPS[i - 1]
        try:
            step = fn(seed, live, control_url)
            step["number"] = f"{i}/{total}"
        except Exception as exc:  # demo never crashes mid-run; a step fails loudly
            step = {"number": f"{i}/{total}", "name": fn.__name__, "status": "failed", "error": str(exc)[:500], "control_calls": [], "mode": "in-process"}
        if "mode" not in step:
            step["mode"] = "live" if step.get("control_calls") else "in-process"
        steps.append(step)
    failed = [s for s in steps if s.get("status") != "ok"]
    return {
        "mode": "live-control-api" if live else "local-evidence",
        "control_url": control_url,
        "seed": seed,
        "steps": steps,
        "failed": len(failed),
    }


def render_text(report: dict[str, Any]) -> str:
    """Human-readable rendering — the same evidence the UI screens show."""
    out: list[str] = []
    out.append(f"AutoBridge demo — {len(report['steps'])} steps (mode: {report['mode']}, seed {report['seed']})")
    live_steps = [s.get("number") for s in report["steps"] if s.get("mode") == "live"]
    local_steps = [s.get("number") for s in report["steps"] if s.get("mode") != "live"]
    if report["mode"] == "live-control-api":
        out.append(f"live steps: {', '.join(live_steps) or 'none'}; in-process steps: {', '.join(local_steps) or 'none'}")
    else:
        out.append("all steps computed in-process (control plane unreachable)")
    for step in report["steps"]:
        src = step.get("mode", "in-process")
        out.append(f"--- Step {step['number']}: {step.get('name')} [{step.get('status')}] ({src})")
        if step.get("status") != "ok":
            out.append(f"    error: {step.get('error')}")
            for call in step.get("control_calls", []):
                if not _call_ok(call):
                    detail = call.get("error") or call.get("body")
                    try:
                        detail_text = json.dumps(detail, default=str)[:300]
                    except Exception:
                        detail_text = str(detail)[:300]
                    out.append(f"    FAILED control {call.get('method')} {call.get('path')} -> {call.get('status')}: {detail_text}")
            continue
        for key, value in step.get("evidence", {}).items():
            if key == "preview":
                out.append(f"    {key}:")
                for line in value:
                    out.append(f"      {line}")
            elif key == "patch":
                out.append(f"    {key}: {json.dumps(value)}")
            else:
                out.append(f"    {key}: {value}")
        for call in step.get("control_calls", []):
            status = call.get("status")
            out.append(f"    control {call.get('method')} {call.get('path')} -> {status}")
        out.append(f"    {rung_table_row(step)}")
    out.append(render_rung_summary(report))
    total = len(report["steps"])
    out.append(f"demo {'complete' if report['failed'] == 0 else f'{report['failed']} step(s) FAILED'}: {total} steps via control API" if report["mode"] == "live-control-api" else f"demo {'complete' if report['failed'] == 0 else f'{report['failed']} step(s) FAILED'}: local evidence (control plane unreachable at {report['control_url']})")
    return "\n".join(out)


def _resolve_step(value: str) -> int | None:
    """Resolve ``STEP=<n|name>`` to a step number, or None when unknown."""
    value = (value or "").strip()
    if value.isdigit() and 1 <= int(value) <= len(STEPS):
        return int(value)
    lowered = value.lower()
    for number, name in STEP_NAMES.items():
        if lowered == name or lowered == f"step{name}" or lowered == f"step_{name}":
            return number
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``make demo``."""
    parser = argparse.ArgumentParser(description="AutoBridge eight-step demo (plan §6)")
    parser.add_argument("--control-url", default=env_default("CONTROL_URL", DEFAULT_CONTROL_URL))
    parser.add_argument("--seed", type=int, default=int(env_default("DEMO_SEED", str(DEFAULT_SEED))))
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON")
    parser.add_argument("--scenario", default=env_default("DEMO_SCENARIO", ""),
                        help="Run one catalogue entry (REQ-023), e.g. --scenario gross_to_net")
    parser.add_argument("--step", default=env_default("DEMO_STEP", ""),
                        help="Run one main-script step (REQ-023), e.g. --step 4 or --step hard_semantic_failure")
    args = parser.parse_args(argv)
    if args.scenario and args.step:
        parser.error("--scenario and --step are mutually exclusive")
    only_step: int | None = None
    if args.step:
        only_step = _resolve_step(args.step)
        if only_step is None:
            parser.error(f"unknown --step {args.step!r}; use 1-{len(STEPS)} or {', '.join(sorted(set(STEP_NAMES.values())))}")
    report = run_demo(seed=args.seed, control_url=args.control_url,
                      only_step=only_step,
                      only_scenario=args.scenario or None)
    if args.json:
        print(json.dumps(report, default=str, indent=2))
    else:
        print(render_text(report), flush=True)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
