"""REQ-020 sub-step 5a — spec v2 runtime against the real sim-meridian.

Proves the generic vocabulary extension (POST + JSON body, ``body_fields``
auth, ``body_cursor`` pagination) actually drives the Meridian-shaped vendor:
a hand-written v2 spec pages through live sim-meridian with the cursor
contract (``Cursor`` = last id, short-page stop) and money arrives as
``Decimal`` end to end. No vendor branch in the engine — the test only uses
generic ``ConnectorSpec`` data plus the documented simulator surface.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import TYPE_CHECKING

from autobridge.runtime.http import HttpError
from autobridge.runtime.interpreter import Interpreter
from autobridge.spec.models import (
    load_spec_from_dict,
    spec_to_json_data,
    validate_against_schema_file,
)
from tests.e2e.conftest import service_url

if TYPE_CHECKING:
    import pytest

CLIENT_TOKEN = "meridian-client-token"
ACCESS_TOKEN = "meridian-access-token"


def _v2_spec_dict(base_url: str) -> dict:
    return {
        "spec_version": 2,
        "connector": {"id": "meridianprobe", "vendor": "MeridianProbe", "runtime_target": "cloud"},
        "source": {
            "base_url": base_url,
            "auth": {
                "type": "body_fields",
                "body_fields": {"ClientToken": "E2E_MERIDIAN_CLIENT", "AccessToken": "E2E_MERIDIAN_ACCESS"},
            },
            "rate_limit": {
                "max_rps": 5,
                "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3},
            },
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "POST",
                "path": "/api/connector/v1/reservations/getAll/2023-06-06",
                "params": {},
                "body": {"Limitation": {"Count": 5}},
                "pagination": {
                    "strategy": "body_cursor",
                    "cursor_path": "Limitation.Cursor",
                    "count_path": "Limitation.Count",
                    "count_value": 5,
                    "next_path": "$.Cursor",
                    "items_path": "$.Reservations",
                    "stop_when": ["null_cursor", "empty_page"],
                },
            },
            "list_order_items": {
                "method": "POST",
                "path": "/api/connector/v1/orderItems/getAll/2023-06-06",
                "params": {},
                "body": {"Limitation": {"Count": 5}},
                "pagination": {
                    "strategy": "body_cursor",
                    "cursor_path": "Limitation.Cursor",
                    "count_path": "Limitation.Count",
                    "count_value": 5,
                    "next_path": "$.Cursor",
                    "items_path": "$.OrderItems",
                    "stop_when": ["null_cursor", "empty_page"],
                },
            },
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.Id",
                "fields": {"reservation_id": {"path": "$.Id"}},
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }


def test_v2_spec_validates_against_schema_file() -> None:
    """The v2 document validates against the committed v2 JSON schema (no stack)."""
    spec = load_spec_from_dict(_v2_spec_dict("https://example.com"))
    validate_against_schema_file(spec_to_json_data(spec))


def test_v2_runtime_pages_live_meridian(e2e_stack: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live sim-meridian: v2 spec walks the cursor contract across pages."""
    assert e2e_stack

    monkeypatch.setenv("E2E_MERIDIAN_CLIENT", CLIENT_TOKEN)
    monkeypatch.setenv("E2E_MERIDIAN_ACCESS", ACCESS_TOKEN)
    base_url = service_url("sim-meridian", "")[:-1]
    spec = load_spec_from_dict(_v2_spec_dict(base_url))
    interp = Interpreter(spec)

    res = interp.execute_operation("list_reservations")
    assert res.pages_fetched >= 2, f"expected multiple pages, got {res.pages_fetched}"
    ids = [r["Id"] for r in res.records]
    assert len(ids) == len(set(ids)), "duplicate ids across pages"
    assert res.stop_reason in ("short_page", "null_cursor", "empty_page")
    assert all("State" in r and "AccountId" in r for r in res.records)

    items = interp.execute_operation("list_order_items")
    assert items.records, "expected order items"
    gross = items.records[0]["UnitAmount"]["GrossValue"]
    assert isinstance(gross, Decimal), f"money must be Decimal, got {type(gross)}"


def test_v2_runtime_wrong_tokens_fail_cleanly(e2e_stack: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrong body tokens → 403 → HttpError naming the status, no secret leak."""
    assert e2e_stack

    monkeypatch.setenv("E2E_MERIDIAN_CLIENT", "wrong")
    monkeypatch.setenv("E2E_MERIDIAN_ACCESS", "wrong")
    base_url = service_url("sim-meridian", "")[:-1]
    spec = load_spec_from_dict(_v2_spec_dict(base_url))
    interp = Interpreter(spec)
    try:
        interp.execute_operation("list_reservations")
    except HttpError as exc:
        assert exc.status_code == 403
        assert "wrong" not in str(exc)
    else:
        raise AssertionError("expected HttpError for wrong tokens")
    # Restore not needed: monkeypatch reverts env after the test.
    _ = os.environ.get("E2E_MERIDIAN_CLIENT")
