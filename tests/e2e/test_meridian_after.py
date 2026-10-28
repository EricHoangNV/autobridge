"""REQ-020 item 6 "After" — the same Meridian docs bootstrap a connector that
passes the verifier gate and syncs Complete, with UTC instants mapped to
property-local business dates.

  * discovered v2 spec passes the oracle gate on baseline (no drift);
  * the same spec fails both Meridian drift scenarios in exactly the
    intended check (it is the correct pre-repair base);
  * an in-process folio whose UTC instant falls on a different local
    business date lands on the property-local date (America/New_York,
    cutoff 04:00 per the discovered spec), never naive UTC;
  * live: the discovered spec syncs Complete against sim-meridian and every
    folio business date matches the property-local derivation.
"""

from __future__ import annotations

import pathlib
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    import pytest

from autobridge.discovery.bootstrap import build_draft_spec
from autobridge.engine.sync_run import execute_sync
from autobridge.runtime.http import HttpClient
from autobridge.spec.models import RetryPolicy, load_spec_from_dict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MERIDIAN_YAML = (REPO_ROOT / "simulators" / "docs" / "meridian-openapi.yaml").read_text(encoding="utf-8")

CLIENT_TOKEN = "meridian-client-token"
ACCESS_TOKEN = "meridian-access-token"


def _discovered_spec_dict() -> dict:
    result = build_draft_spec(
        MERIDIAN_YAML,
        source="simulators/docs/meridian-openapi.yaml",
        connector_id="meridianpms",
        spec_vocabulary="v2",
    )
    assert result.success, f"visible_errors={result.visible_errors}"
    return dict(result.draft_spec)


def _fake_one_folio(posted_utc: str) -> HttpClient:
    """One reservation + customer + folio + enterprise (America/New_York)."""
    reservation = {
        "Id": "RES-9",
        "ServiceId": "SVC-prop-009",
        "AccountId": "ACC-RES-9",
        "Number": "900009",
        "State": "Confirmed",
        "ScheduledStartUtc": "2026-10-27T15:00:00Z",
        "ScheduledEndUtc": "2026-10-29T11:00:00Z",
        "UpdatedUtc": "2026-10-26T10:00:00Z",
    }
    customer = {
        "Id": "ACC-RES-9",
        "FirstName": "Ada",
        "LastName": "Lovelace",
        "Email": None,
        "Phone": None,
        "UpdatedUtc": "2026-10-26T10:00:00Z",
    }
    folio = {
        "Id": "CHG-9",
        "AccountId": "ACC-RES-9",
        "ServiceId": "SVC-prop-009",
        "Name": "Room charge",
        "UnitCount": 1,
        "UnitAmount": {"Currency": "USD", "NetValue": "90.91", "GrossValue": "100.00", "TaxValues": []},
        "PostedUtc": posted_utc,
    }
    enterprise = {"Id": "prop-009", "Name": "Test", "TimeZone": "America/New_York", "CurrencyCode": "USD"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "reservations" in path:
            return httpx.Response(200, json={"Reservations": [reservation], "Cursor": None})
        if "customers" in path:
            return httpx.Response(200, json={"Customers": [customer], "Cursor": None})
        if "orderItems" in path:
            return httpx.Response(200, json={"OrderItems": [folio], "Cursor": None})
        if "configuration" in path:
            return httpx.Response(200, json={"Enterprises": [enterprise]})
        raise AssertionError(f"unexpected path {path!r}")  # pragma: no cover

    transport = httpx.MockTransport(handler)

    def factory(**kw: Any) -> httpx.Client:
        return httpx.Client(transport=transport, timeout=kw.get("timeout", 5))

    return HttpClient(
        "https://meridian.example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def test_after_gate_passes_and_drifts_fail_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate (oracle) on the discovered spec: baseline passes, drifts fail exactly."""
    monkeypatch.setenv("MERIDIAN_CLIENT_TOKEN", CLIENT_TOKEN)
    monkeypatch.setenv("MERIDIAN_ACCESS_TOKEN", ACCESS_TOKEN)
    from verifier.oracle import oracle_verdict

    spec = _discovered_spec_dict()
    load_spec_from_dict(spec)
    verdict, score, failed = oracle_verdict("baseline", spec)
    assert verdict == "pass", failed
    assert score == 1.0 and not failed
    v1, _, f1 = oracle_verdict("meridian_state_renamed", spec)
    assert v1 == "fail" and {f["name"] for f in f1} == {"field_missing"}
    v2, _, f2 = oracle_verdict("meridian_cursor_renamed", spec)
    assert v2 == "fail" and {f["name"] for f in f2} == {"pagination_shape"}


def test_after_business_date_is_property_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """UTC instant -> property-local business date, never naive UTC."""
    monkeypatch.setenv("MERIDIAN_CLIENT_TOKEN", CLIENT_TOKEN)
    monkeypatch.setenv("MERIDIAN_ACCESS_TOKEN", ACCESS_TOKEN)
    from simulators.common.generator import business_date_for_posted_at

    spec = _discovered_spec_dict()
    # 2026-10-26T02:30Z is Oct 25 22:30 in New York: before the 04:00
    # cutoff, so the business date is Oct 25, not the UTC date Oct 26.
    posted = "2026-10-26T02:30:00Z"
    assert business_date_for_posted_at(posted, "America/New_York") == "2026-10-25"
    summary = execute_sync(spec, _fake_one_folio(posted))
    assert summary["outcome"] == "Complete", summary.get("unmet_criteria")
    folios = summary["canonical_records"]["FolioCharge"]
    assert len(folios) == 1
    assert folios[0]["business_date"] == "2026-10-25"
    assert folios[0]["amount"] == Decimal("100.00")
    assert isinstance(folios[0]["amount"], Decimal)


def test_after_live_sync_complete_and_dates_local(e2e_stack: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live: discovered spec syncs Complete; folio dates match property tz."""
    assert e2e_stack
    from tests.e2e.conftest import service_url

    monkeypatch.setenv("MERIDIAN_CLIENT_TOKEN", CLIENT_TOKEN)
    monkeypatch.setenv("MERIDIAN_ACCESS_TOKEN", ACCESS_TOKEN)
    import urllib.parse as _urlparse

    from autobridge.runtime.http import HttpClient as _LiveHttp
    from simulators.common.generator import business_date_for_posted_at

    spec = _discovered_spec_dict()
    doc_base = _urlparse.urlsplit(spec["source"]["base_url"])
    assert doc_base.path, f"docs must declare the API path prefix, got {spec['source']['base_url']!r}"
    spec["source"]["base_url"] = service_url("sim-meridian", "")[:-1] + doc_base.path
    model = load_spec_from_dict(spec)
    http = _LiveHttp(
        base_url=model.source.base_url,
        auth=model.source.auth,
        retry=model.source.rate_limit.retry,
    )
    summary = execute_sync(spec, http)
    assert summary["outcome"] == "Complete", summary.get("unmet_criteria")
    folios = summary["canonical_records"]["FolioCharge"]
    assert folios, "expected folios from live sim-meridian"
    # The discovered spec declares a static enterprise tz (5c design); the
    # engine derives business dates through it, never naive UTC.
    tz = (spec["source"].get("timezone_source") or {}).get("value", "UTC")
    assert tz, "discovered spec must declare a timezone_source"
    checked = 0
    for folio in folios:
        posted = folio.get("posted_at")
        if posted:
            assert folio.get("business_date") == business_date_for_posted_at(str(posted), str(tz)), folio
            checked += 1
    assert checked > 0, "expected folios with posted_at to check"
