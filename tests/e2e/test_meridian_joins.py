"""REQ-020 sub-step 5c — discovered joins run against Meridian data.

The v2 spec built from Meridian's docs (no hand-written paths) enriches
reservations through all three proposed joins: guest name from customers,
total + currency from summed order items, and each folio's parent id from
reservations. The in-process test pins this on fake Meridian-shaped pages;
the live test replays the same discovered spec against sim-meridian.
"""

from __future__ import annotations

import os
import pathlib
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

from autobridge.discovery.bootstrap import build_draft_spec
from autobridge.engine.sync_run import execute_sync
from autobridge.runtime.http import HttpClient
from autobridge.spec.models import RetryPolicy, load_spec_from_dict

if TYPE_CHECKING:
    import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MERIDIAN_YAML = (REPO_ROOT / "simulators" / "docs" / "meridian-openapi.yaml").read_text(encoding="utf-8")

CLIENT_TOKEN = "meridian-client-token"
ACCESS_TOKEN = "meridian-access-token"

# Fake Meridian-shaped pages: one reservation + account, one customer,
# two order items whose gross sums to the reservation total.
FAKE_RESERVATIONS = [
    {
        "Id": "RES-1",
        "ServiceId": "SVC-prop-001",
        "AccountId": "ACC-RES-1",
        "Number": "100001",
        "State": "Confirmed",
        "ScheduledStartUtc": "2026-10-27T15:00:00Z",
        "ScheduledEndUtc": "2026-10-29T11:00:00Z",
        "UpdatedUtc": "2026-10-26T10:00:00Z",
    }
]
FAKE_CUSTOMERS = [
    {
        "Id": "ACC-RES-1",
        "FirstName": "Ada",
        "LastName": "Lovelace",
        "Email": None,
        "Phone": None,
        "UpdatedUtc": "2026-10-26T10:00:00Z",
    }
]
FAKE_ITEMS = [
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
FAKE_ENTERPRISES = [{"Id": "prop-001", "Name": "Test", "TimeZone": "UTC", "CurrencyCode": "USD"}]


def _discovered_spec_dict() -> dict:
    result = build_draft_spec(
        MERIDIAN_YAML,
        source="simulators/docs/meridian-openapi.yaml",
        connector_id="meridianpms",
        spec_vocabulary="v2",
    )
    assert result.success, f"visible_errors={result.visible_errors}"
    spec = dict(result.draft_spec)
    assert spec is not None
    return spec


def _with_host(spec: dict, host: str) -> dict:
    """Point the documented base URL at *host*, keeping its path prefix."""
    import urllib.parse as _urlparse

    doc_base = _urlparse.urlsplit(spec["source"]["base_url"])
    assert doc_base.path, f"docs must declare the API path prefix, got {spec['source']['base_url']!r}"
    spec["source"]["base_url"] = host + doc_base.path
    return spec


def _fake_meridian_http() -> HttpClient:
    """httpx MockTransport serving the fake pages with the cursor contract."""

    def handler(request: httpx.Request) -> httpx.Response:
        body: dict = {}
        try:
            import json as _json

            body = _json.loads(request.content.decode() or "{}")
        except Exception:
            body = {}
        path = request.url.path
        if "reservations" in path:
            rows, key = FAKE_RESERVATIONS, "Reservations"
        elif "customers" in path:
            rows, key = FAKE_CUSTOMERS, "Customers"
        elif "orderItems" in path:
            rows, key = FAKE_ITEMS, "OrderItems"
        elif "configuration" in path:
            return httpx.Response(200, json={"Enterprises": FAKE_ENTERPRISES})
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

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    return HttpClient(
        "https://meridian.example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def _assert_joined_canonical(summary: dict) -> None:
    assert summary["outcome"] == "Complete", summary.get("unmet_criteria")
    reservations = summary["canonical_records"]["Reservation"]
    assert len(reservations) == 1
    res = reservations[0]
    assert res["guest_name"] == "Ada Lovelace"
    assert res["total_amount"] == Decimal("20.00")
    assert isinstance(res["total_amount"], Decimal)
    assert res["currency"] == "USD"
    folios = summary["canonical_records"]["FolioCharge"]
    assert len(folios) == 2
    assert {f["reservation_id"] for f in folios} == {"RES-1"}
    assert folios[0]["amount"] == Decimal("19.99")


def test_discovered_joins_run_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovered v2 spec + joins resolve on fake Meridian-shaped pages."""
    monkeypatch.setenv("MERIDIAN_CLIENT_TOKEN", CLIENT_TOKEN)
    monkeypatch.setenv("MERIDIAN_ACCESS_TOKEN", ACCESS_TOKEN)
    spec = _discovered_spec_dict()
    load_spec_from_dict(spec)
    summary = execute_sync(spec, _fake_meridian_http())
    _assert_joined_canonical(summary)


def test_discovered_joins_run_live(e2e_stack: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same discovered spec against live sim-meridian: names, sums, Decimal."""
    assert e2e_stack
    from tests.e2e.conftest import service_url

    monkeypatch.setenv("MERIDIAN_CLIENT_TOKEN", CLIENT_TOKEN)
    monkeypatch.setenv("MERIDIAN_ACCESS_TOKEN", ACCESS_TOKEN)
    # Keep the documented path prefix (/api/connector/v1 from the docs'
    # servers URL); only the host:port points at the live simulator.
    live_host = service_url("sim-meridian", "")[:-1]
    spec = _with_host(_discovered_spec_dict(), live_host)
    model = load_spec_from_dict(spec)
    from autobridge.runtime.http import HttpClient as _LiveHttp

    http = _LiveHttp(
        base_url=model.source.base_url,
        auth=model.source.auth,
        retry=model.source.rate_limit.retry,
    )
    summary = execute_sync(spec, http)
    assert summary["outcome"] == "Complete", summary.get("unmet_criteria")
    reservations = summary["canonical_records"]["Reservation"]
    assert reservations, "expected reservations from live sim-meridian"
    # guest_name is nullable by design (~10% of accounts are nameless);
    # named accounts must resolve through the customer join.
    named = [r for r in reservations if r.get("guest_name")]
    assert named, "expected some reservations with joined guest names"
    for res in reservations:
        assert isinstance(res["total_amount"], Decimal)
    # Reservation totals equal the summed gross of their order items.
    by_account: dict[str, Decimal] = {}
    for folio in summary["canonical_records"]["FolioCharge"]:
        by_account[folio["reservation_id"]] = by_account.get(folio["reservation_id"], Decimal("0")) + folio["amount"]
    for res in reservations:
        assert res["total_amount"] == by_account[res["reservation_id"]]
    _ = os.environ.get("MERIDIAN_CLIENT_TOKEN")
