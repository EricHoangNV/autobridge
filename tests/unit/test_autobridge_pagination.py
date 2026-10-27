"""Tests for pagination helpers (P1.W3.3)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from autobridge.runtime.interpreter import Interpreter
from autobridge.runtime.pagination import (
    PaginationState,
    advance_state,
    build_request_params,
    evaluate_stop,
    initial_state,
    is_null_cursor,
    is_repeated_cursor,
)
from autobridge.spec.models import Pagination


def _pagination(
    strategy: str,
    *,
    stop_when: list[str] | None = None,
    cursor_param: str | None = "cursor",
    offset_param: str | None = "offset",
    page_param: str | None = "page",
    limit_param: str | None = "limit",
    next_path: str | None = "$.meta.next",
    items_path: str = "$.data",
) -> Pagination:
    return Pagination(
        strategy=strategy,  # type: ignore[arg-type]
        cursor_param=cursor_param if strategy == "cursor" else None,
        offset_param=offset_param if strategy == "offset" else None,
        page_param=page_param if strategy == "page" else None,
        limit_param=limit_param,
        next_path=next_path,
        items_path=items_path,
        stop_when=stop_when or ["null_cursor"],
    )


# ---------------------------------------------------------------------------
# initial_state / is_null_cursor / is_repeated_cursor
# ---------------------------------------------------------------------------


def test_initial_state_ok() -> None:
    s = initial_state("cursor")
    assert s.strategy == "cursor"
    assert s.cursor is None
    assert s.offset == 0
    assert s.page == 1


def test_initial_state_unknown() -> None:
    with pytest.raises(ValueError, match="unknown pagination"):
        initial_state("bogus")  # type: ignore[arg-type]


def test_is_null_cursor() -> None:
    assert is_null_cursor(None) is True
    assert is_null_cursor("") is True
    assert is_null_cursor("abc") is False
    assert is_null_cursor(0) is False


def test_is_repeated_cursor_true() -> None:
    s = PaginationState(strategy="cursor", cursor="A", previous_cursor=None)
    assert is_repeated_cursor(s, "A") is True
    s2 = PaginationState(strategy="cursor", cursor="B", previous_cursor="A")
    assert is_repeated_cursor(s2, "A") is True  # repeats previous


def test_is_repeated_cursor_false() -> None:
    s = PaginationState(strategy="cursor", cursor="A", previous_cursor=None)
    assert is_repeated_cursor(s, "B") is False
    assert is_repeated_cursor(s, None) is False
    s_off = PaginationState(strategy="offset", cursor=None)
    assert is_repeated_cursor(s_off, "A") is False


def test_copy() -> None:
    s = PaginationState(strategy="cursor", cursor="x", offset=5, page=2, previous_cursor="y", pages_fetched=3)
    c = s.copy()
    assert c.cursor == s.cursor
    assert c is not s


# ---------------------------------------------------------------------------
# build_request_params
# ---------------------------------------------------------------------------


def test_build_params_cursor_first_page_no_cursor() -> None:
    pag = _pagination("cursor", stop_when=["null_cursor"])
    state = initial_state("cursor")
    params = build_request_params(state, pag, {"foo": "bar"}, 50)
    assert params == {"foo": "bar", "limit": "50"}
    # cursor not sent on first page
    assert "cursor" not in params


def test_build_params_cursor_with_cursor() -> None:
    pag = _pagination("cursor")
    state = PaginationState(strategy="cursor", cursor="tok123")
    params = build_request_params(state, pag, {}, 10)
    assert params["cursor"] == "tok123"
    assert params["limit"] == "10"


def test_build_params_offset() -> None:
    pag = _pagination("offset")
    state = PaginationState(strategy="offset", offset=20)
    params = build_request_params(state, pag, {}, 25)
    assert params["offset"] == "20"
    assert params["limit"] == "25"


def test_build_params_page() -> None:
    pag = _pagination("page")
    state = PaginationState(strategy="page", page=3)
    params = build_request_params(state, pag, {}, 10)
    assert params["page"] == "3"
    assert params["limit"] == "10"


def test_build_params_base_preserved() -> None:
    pag = _pagination("cursor")
    state = initial_state("cursor")
    params = build_request_params(state, pag, {"since": "2024-01-01"}, 50)
    assert params["since"] == "2024-01-01"


# ---------------------------------------------------------------------------
# evaluate_stop
# ---------------------------------------------------------------------------


def test_evaluate_empty_page() -> None:
    state = initial_state("cursor")
    should, reason, repeated = evaluate_stop(
        state=state, items=[], next_token="abc", stop_when={"empty_page"}, max_pages=10
    )
    assert should is True
    assert reason == "empty_page"
    assert repeated is False


def test_evaluate_null_cursor_none() -> None:
    state = initial_state("cursor")
    should, reason, _ = evaluate_stop(
        state=state, items=[{"id": 1}], next_token=None, stop_when={"null_cursor"}, max_pages=10
    )
    assert should is True
    assert reason == "null_cursor"


def test_evaluate_null_cursor_empty_string() -> None:
    state = initial_state("cursor")
    should, reason, _ = evaluate_stop(
        state=state, items=[{"id": 1}], next_token="", stop_when={"null_cursor"}, max_pages=10
    )
    assert should is True
    assert reason == "null_cursor"


def test_evaluate_no_next() -> None:
    state = initial_state("cursor")
    should, reason, _ = evaluate_stop(
        state=state, items=[{"id": 1}], next_token=None, stop_when={"no_next"}, max_pages=10
    )
    assert should is True
    assert reason == "no_next"


def test_evaluate_repeated_cursor_current() -> None:
    state = PaginationState(strategy="cursor", cursor="A", previous_cursor=None, pages_fetched=1)
    should, reason, repeated = evaluate_stop(
        state=state, items=[{"id": 1}], next_token="A", stop_when={"repeated_cursor"}, max_pages=10
    )
    assert should is True
    assert reason == "repeated_cursor"
    assert repeated is True


def test_evaluate_repeated_cursor_previous() -> None:
    state = PaginationState(strategy="cursor", cursor="B", previous_cursor="A", pages_fetched=2)
    should, reason, repeated = evaluate_stop(
        state=state, items=[{"id": 1}], next_token="A", stop_when={"repeated_cursor"}, max_pages=10
    )
    assert should is True
    assert repeated is True


def test_evaluate_repeated_not_in_stop_when() -> None:
    state = PaginationState(strategy="cursor", cursor="A", pages_fetched=1)
    should, reason, repeated = evaluate_stop(
        state=state, items=[{"id": 1}], next_token="A", stop_when={"null_cursor"}, max_pages=10
    )
    # Not considered repeated when guard not in stop_when
    assert should is False or reason != "repeated_cursor"
    assert repeated is False


def test_evaluate_page_limit() -> None:
    state = PaginationState(strategy="cursor", pages_fetched=5)
    should, reason, _ = evaluate_stop(
        state=state, items=[{"id": 1}], next_token="nxt", stop_when={"null_cursor"}, max_pages=5
    )
    assert should is True
    assert reason == "page_limit"


def test_evaluate_no_next_token_implicit() -> None:
    state = PaginationState(strategy="cursor", pages_fetched=1)
    should, reason, _ = evaluate_stop(
        state=state, items=[{"id": 1}], next_token=None, stop_when=set(), max_pages=10
    )
    assert should is True
    assert reason == "no_next_token"


def test_evaluate_empty_page_implicit() -> None:
    state = PaginationState(strategy="cursor", pages_fetched=1)
    should, reason, _ = evaluate_stop(
        state=state, items=[], next_token="abc", stop_when=set(), max_pages=10
    )
    assert should is True
    assert reason == "empty_page_implicit"


def test_evaluate_continue() -> None:
    state = PaginationState(strategy="cursor", cursor=None, pages_fetched=1)
    should, reason, repeated = evaluate_stop(
        state=state, items=[{"id": 1}], next_token="nxt", stop_when={"null_cursor"}, max_pages=10
    )
    assert should is False
    assert reason is None
    assert repeated is False


# ---------------------------------------------------------------------------
# advance_state
# ---------------------------------------------------------------------------


def test_advance_offset_int() -> None:
    state = PaginationState(strategy="offset", offset=0, pages_fetched=1)
    nxt = advance_state(state, 20, [{"id": 1}], 10)
    assert nxt.offset == 20


def test_advance_offset_fallback() -> None:
    state = PaginationState(strategy="offset", offset=10, pages_fetched=1)
    nxt = advance_state(state, "not-int", [{"id": 1}, {"id": 2}], 10)
    assert nxt.offset == 12  # 10 + 2 items
    nxt2 = advance_state(state, "bad", [], 10)
    assert nxt2.offset == 20  # 10 + limit fallback


def test_advance_cursor() -> None:
    state = PaginationState(strategy="cursor", cursor="A", pages_fetched=1)
    nxt = advance_state(state, "B", [{"id": 1}], 10)
    assert nxt.cursor == "B"
    assert nxt.previous_cursor == "A"
    # first page previous is None
    s0 = PaginationState(strategy="cursor", cursor=None)
    nxt0 = advance_state(s0, "A", [{"id": 1}], 10)
    assert nxt0.cursor == "A"
    assert nxt0.previous_cursor is None


def test_advance_page() -> None:
    state = PaginationState(strategy="page", page=1)
    nxt = advance_state(state, "ignored", [{"id": 1}], 10)
    assert nxt.page == 2


# ---------------------------------------------------------------------------
# Interpreter integration — offset/cursor/page via pagination helpers
# ---------------------------------------------------------------------------


def _fake_http(pages: list[dict]) -> Any:
    idx = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        if idx["i"] >= len(pages):
            return httpx.Response(200, json={"data": [], "meta": {"next_cursor": None}})
        body = pages[idx["i"]]
        idx["i"] += 1
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, timeout=kwargs.get("timeout", 5))

    from autobridge.runtime.http import HttpClient
    from autobridge.spec.models import RetryPolicy

    return HttpClient(
        "https://example.com",
        retry=RetryPolicy(statuses=[429, 503], backoff="exponential", max_attempts=3),
        client_factory=factory,
        sleep_fn=lambda _: None,
    )


def _spec_dict(pagination: dict | None = None) -> dict:
    return {
        "spec_version": 1,
        "connector": {"id": "testconn", "vendor": "Test", "runtime_target": "cloud"},
        "source": {
            "base_url": "https://example.com",
            "auth": {"type": "bearer", "credential_ref": "TEST_TOKEN"},
            "rate_limit": {"max_rps": 5, "retry": {"statuses": [429, 503], "backoff": "exponential", "max_attempts": 3}},
            "timezone_source": {"kind": "static", "value": "UTC"},
        },
        "operations": {
            "list_reservations": {
                "method": "GET",
                "path": "/reservations",
                "params": {},
                "pagination": pagination,
            }
        },
        "entities": {
            "Reservation": {
                "from": "list_reservations",
                "key": "$.reservation_id",
                "fields": {"reservation_id": {"path": "$.reservation_id"}},
            }
        },
        "capabilities": {"read": ["Reservation"]},
        "health": {"canary": {"every": "60s", "operation": "list_reservations"}, "fingerprint": {}},
    }


def test_interpreter_offset_via_pagination(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "offset",
        "offset_param": "offset",
        "limit_param": "limit",
        "next_path": "$.meta.next_offset",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict(pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [
        {"data": [{"id": 1}], "meta": {"next_offset": 1}},
        {"data": [{"id": 2}], "meta": {"next_offset": None}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert len(result.records) == 2
    assert result.stop_reason == "null_cursor"


def test_interpreter_cursor_via_pagination(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict(pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [
        {"data": [{"id": 1}], "meta": {"next_cursor": "abc"}},
        {"data": [{"id": 2}], "meta": {"next_cursor": None}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert len(result.records) == 2
    assert result.stop_reason == "null_cursor"
    assert result.repeated_cursor is False


def test_interpreter_page_via_pagination(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "page",
        "page_param": "page",
        "limit_param": "limit",
        "next_path": "$.meta.next_page",
        "items_path": "$.data",
        "stop_when": ["null_cursor"],
    }
    spec_dict = _spec_dict(pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [
        {"data": [{"id": 1}], "meta": {"next_page": 2}},
        {"data": [{"id": 2}], "meta": {"next_page": None}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert len(result.records) == 2


def test_interpreter_repeated_cursor_stops(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TEST_TOKEN", "tok")
    pagination = {
        "strategy": "cursor",
        "cursor_param": "cursor",
        "next_path": "$.meta.next_cursor",
        "items_path": "$.data",
        "stop_when": ["repeated_cursor"],
    }
    spec_dict = _spec_dict(pagination)
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    pages = [
        {"data": [{"id": 1}], "meta": {"next_cursor": "abc"}},
        {"data": [{"id": 2}], "meta": {"next_cursor": "abc"}},
    ]
    http = _fake_http(pages)
    interp = Interpreter(spec, http_client=http)
    result = interp.execute_operation("list_reservations")
    assert result.repeated_cursor is True
    assert result.stop_reason == "repeated_cursor"
