"""REQ-022 sub-step C "after": the same FIAS docs bootstrap a connector that
passes the gate and syncs Complete, with no FIAS-specific branch outside
the generic event-driven transport.

  * the v3 build of ``simulators/docs/fias-pabx.md`` compiles a valid
    spec (EVENT feeds, edge_relay auth, link data, command templates);
  * the discovered spec syncs ``Complete`` in-process over scripted PABX
    records: minor-unit amounts land exact, 02:30 local lands on the prior
    business date, resends fold;
  * live: burst postings from the real PABX flow through the edge agent
    into the relay, and the same discovered spec drains them to
    ``Complete`` via the relay feed reader.

Trust boundary: this file lives in tests/e2e and may import the engine +
simulators for verification; the engine itself never imports
verifier/simulators.
"""

from __future__ import annotations

import os
import pathlib
import time
import urllib.request
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

from autobridge.discovery.bootstrap import build_draft_spec
from autobridge.engine.sync_run import execute_sync
from autobridge.spec.models import load_spec_from_dict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FIAS_MD_PATH = REPO_ROOT / "simulators" / "docs" / "fias-pabx.md"
FIAS_MD = FIAS_MD_PATH.read_text(encoding="utf-8")


def _discovered_spec_dict() -> dict[str, Any]:
    result = build_draft_spec(
        FIAS_MD,
        source="simulators/docs/fias-pabx.md",
        connector_id="fiaspabx",
        spec_vocabulary="v3",
    )
    assert result.success, f"visible_errors={result.visible_errors}"
    spec = dict(result.draft_spec)
    load_spec_from_dict(spec)
    return spec


class _ScriptFeed:
    def __init__(self, ps: list[dict[str, Any]], re_: list[dict[str, Any]]) -> None:
        self._by_record = {"PS": ps, "RE": re_}

    def read(self, feed: Any, operation_name: str) -> list[dict[str, Any]]:
        rec = feed["record"] if isinstance(feed, dict) else feed.record
        records = self._by_record.get(rec)
        if records is None:
            raise KeyError(f"unknown feed {rec!r} for {operation_name!r}")
        return [dict(r) for r in records]


def test_after_sync_complete_in_process() -> None:
    """Discovered v3 spec syncs Complete: exact money, local dates, folding."""
    ps = [
        {"RN": "101", "P#": "000417", "DA": "261028", "TI": "231504",
         "TA": "1250", "DD": "004930XXXXXX", "DU": "000312", "SO": "TEL"},
        {"RN": "101", "P#": "000417", "DA": "261028", "TI": "231504",
         "TA": "1250", "DD": "004930XXXXXX", "DU": "000312", "SO": "TEL"},
        {"RN": "102", "P#": "000418", "DA": "261029", "TI": "023000", "TA": "899", "SO": "MINI"},
    ]
    re_ = [{"RN": "101", "RS": "3", "DA": "261028", "TI": "180000"}]
    out = execute_sync(_discovered_spec_dict(), None, feed_reader=_ScriptFeed(ps, re_))
    assert out["outcome"] == "Complete", out.get("unmet_criteria")
    folios = out["canonical_records"]["FolioCharge"]
    assert len(folios) == 2
    assert folios[0]["amount"] == Decimal("12.50")
    assert isinstance(folios[0]["amount"], Decimal)
    assert folios[0]["business_date"] == "2026-10-28"
    assert folios[1]["business_date"] == "2026-10-28"
    assert folios[0]["posted_at"] == "2026-10-29T03:15:04+00:00"
    for folio in folios:
        assert folio["currency"] == "USD" and folio["property_id"] == "prop-001"


def _wait_for(pred: Any, timeout: float = 60.0, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value:
            return value
        assert time.monotonic() < deadline, f"{what} never became true"
        time.sleep(2.0)


def test_after_live_postings_drain_to_complete(e2e_stack: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live: burst PS postings through agent+relay drain to Complete."""
    assert e2e_stack
    import httpx

    from tests.e2e.conftest import admin_headers, admin_url, control_headers

    # The agent ↔ PABX link must be up (edge agent heartbeats).
    agents = _wait_for(
        lambda: [
            a for a in httpx.get("http://localhost:8101/edge/agents",
                                 headers=control_headers(), timeout=15.0).json()["agents"]
            if a["agent_id"] == "grand-plaza-pabx" and a["link_up"] in (1, True)
        ],
        timeout=90.0,
        what="agent↔PABX link",
    )
    assert agents, "live agent never linked to the PABX"

    before = httpx.get("http://localhost:8101/edge/postings",
                       params={"agent_id": "grand-plaza-pabx"},
                       headers=control_headers(), timeout=15.0).json()["postings"]
    before_keys = {p["idempotency_key"] for p in before}

    # Queue burst postings on the PABX (admin fault hook, like the other sims).
    req = urllib.request.Request(
        admin_url("sim-fias-pabx", "/admin/fias/faults"),
        data=b'{"burst_postings": 3}',
        headers={**dict(admin_headers()), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200

    fresh = _wait_for(
        lambda: [
            p for p in httpx.get("http://localhost:8101/edge/postings",
                                 params={"agent_id": "grand-plaza-pabx"},
                                 headers=control_headers(), timeout=15.0).json()["postings"]
            if p["idempotency_key"] not in before_keys
        ],
        timeout=90.0,
        what="burst postings in relay",
    )
    assert len(fresh) >= 1
    assert {p["record_type"] for p in fresh} <= {"PS"}

    # Same discovered spec drains the relay to Complete (no HTTP involved).
    from autobridge.runtime.feeds import RelayFeedReader

    monkeypatch.setenv("EDGE_RELAY_URL", "http://localhost:8120")
    token = os.environ.get("AUTOBRIDGE_CONTROL_TOKEN", "") or "e2e-open-relay"
    spec = _discovered_spec_dict()
    ref = spec["source"]["auth"]["credential_ref"]
    assert ref, "discovered edge_relay auth must name a credential_ref"
    monkeypatch.setenv(ref, token)
    reader = RelayFeedReader.from_env(spec["source"]["auth"])
    out = execute_sync(spec, None, feed_reader=reader)
    assert out["outcome"] == "Complete", out.get("unmet_criteria")
    assert out["records_observed"].get("FolioCharge", 0) >= 1
