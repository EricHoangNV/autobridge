"""Pagination loop helpers for offset / cursor / page strategies (P1.W3.3).

The interpreter delegates per-page bookkeeping to this module so the
``stop_when`` guard semantics are centralized, documented and tested in
isolation:

* ``offset`` — ``offset_param`` advances by integer ``next_offset`` or by
  ``len(items)`` fallback.
* ``cursor`` — ``cursor_param`` is sent only after the first page; the
  guard ``repeated_cursor`` fires when ``next_cursor == cursor`` (same
  token twice) or ``next_cursor == previous_cursor`` (cycle).
* ``page`` — ``page_param`` increments by 1 each iteration.

``stop_when`` guards (all are names, never expressions):

* ``null_cursor`` — ``next_token`` is ``None`` or ``""``.
* ``repeated_cursor`` — see above (cursor strategy only).
* ``empty_page`` — current page returned no items.
* ``no_next`` — ``next_token`` is ``None`` (same as ``null_cursor`` but
  without the empty-string case).
* ``page_limit`` — handled by the ``max_pages`` cap.

A loop without a ``stop_when`` entry for ``repeated_cursor`` is still
terminated by the implicit ``no_next_token`` guard, but it will not be
reported as a ``repeated_cursor`` stop — callers surface the flag so the
sync layer can mark ``Degraded`` with ``no_loss``.

Trust boundary: no import of ``verifier`` or ``simulators``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class PaginationState:
    """Mutable pagination cursor across pages."""

    strategy: str
    offset: int = 0
    cursor: str | None = None
    page: int = 1
    previous_cursor: str | None = None
    pages_fetched: int = 0

    def copy(self) -> PaginationState:
        return PaginationState(
            strategy=self.strategy,
            offset=self.offset,
            cursor=self.cursor,
            page=self.page,
            previous_cursor=self.previous_cursor,
            pages_fetched=self.pages_fetched,
        )


def initial_state(strategy: str) -> PaginationState:
    """Initial state for *strategy* (offset | cursor | page)."""
    if strategy not in ("offset", "cursor", "page"):
        raise ValueError(f"unknown pagination strategy {strategy!r}")
    return PaginationState(strategy=strategy)


def build_request_params(
    state: PaginationState,
    pagination: Any,
    base_params: dict[str, Any],
    limit_value: int,
) -> dict[str, Any]:
    """Merge *base_params* with pagination params for the current page."""
    page_params = dict(base_params)
    strategy = state.strategy
    if strategy == "offset":
        if pagination.offset_param:
            page_params[pagination.offset_param] = str(state.offset)
        if pagination.limit_param:
            page_params[pagination.limit_param] = str(limit_value)
    elif strategy == "cursor":
        if state.cursor is not None and pagination.cursor_param:
            page_params[pagination.cursor_param] = state.cursor
        if pagination.limit_param:
            page_params[pagination.limit_param] = str(limit_value)
    elif strategy == "page":
        if pagination.page_param:
            page_params[pagination.page_param] = str(state.page)
        if pagination.limit_param:
            page_params[pagination.limit_param] = str(limit_value)
    return page_params


def is_null_cursor(next_token: Any) -> bool:
    """True when *next_token* is None or empty string."""
    return next_token is None or (isinstance(next_token, str) and next_token == "")


def is_repeated_cursor(
    state: PaginationState,
    next_token: Any,
) -> bool:
    """True when *next_token* repeats the current or previous cursor.

    Only meaningful for cursor strategy.
    """
    if state.strategy != "cursor":
        return False
    if next_token is None:
        return False
    cur_str = str(state.cursor) if state.cursor is not None else None
    nxt_str = str(next_token)
    if cur_str is not None and nxt_str == cur_str:
        return True
    return bool(state.previous_cursor is not None and nxt_str == state.previous_cursor)


def evaluate_stop(
    *,
    state: PaginationState,
    items: list[Any],
    next_token: Any,
    stop_when: set[str],
    max_pages: int,
) -> tuple[bool, str | None, bool]:
    """Evaluate stop guards before advancing.

    Returns (should_stop, stop_reason, repeated_flag).
    The caller must break before advancing when should_stop is True.
    Checks are ordered to match the interpreter's original precedence:
    empty_page -> null_cursor -> no_next -> repeated_cursor -> page_limit -> implicit no_next
    """
    # empty_page guard
    if "empty_page" in stop_when and not items:
        return True, "empty_page", False

    # null_cursor guard
    if "null_cursor" in stop_when and is_null_cursor(next_token):
        return True, "null_cursor", False

    # no_next guard
    if "no_next" in stop_when and next_token is None:
        return True, "no_next", False

    # repeated_cursor guard (cursor only)
    if "repeated_cursor" in stop_when and is_repeated_cursor(state, next_token):
        return True, "repeated_cursor", True

    # page_limit guard (before implicit checks so capped loop reports page_limit)
    if state.pages_fetched >= max_pages:
        return True, "page_limit", False

    # implicit guard: no next token at all
    if next_token is None:
        return True, "no_next_token", False

    # implicit empty page when not in stop_when (safety)
    if not items and "empty_page" not in stop_when:
        return True, "empty_page_implicit", False

    return False, None, False


def advance_state(
    state: PaginationState,
    next_token: Any,
    items: list[Any],
    limit_value: int,
) -> PaginationState:
    """Advance *state* to the next page based on *next_token*.

    *next_token* is the value extracted via ``next_path`` from the current
    response. For offset it is the next offset integer; for cursor it is the
    next cursor string; page strategy ignores it (increments by 1).
    """
    nxt = state.copy()
    strategy = state.strategy
    if strategy == "offset":
        try:
            nxt.offset = int(next_token)
        except (TypeError, ValueError):
            # Fallback mirrors interpreter: offset + len(items) or limit
            nxt.offset = state.offset + (len(items) if items else limit_value)
        nxt.previous_cursor = str(nxt.offset)
    elif strategy == "cursor":
        nxt.previous_cursor = str(state.cursor) if state.cursor is not None else None
        nxt.cursor = str(next_token)
    elif strategy == "page":
        nxt.page = state.page + 1
    return nxt
