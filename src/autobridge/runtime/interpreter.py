"""ConnectorSpec interpreter — executes operations with retry + pagination (P1.W3.1).

- No :mod:`autobridge.llm` import; reference sync stays ``ModelCall == 0``.
- HTTP is delegated to :class:`autobridge.runtime.http.HttpClient` which
  honours ``rate_limit.retry`` (429/503 + backoff + max_attempts).
- Pagination loop handles ``offset`` / ``cursor`` / ``page`` strategies with
  ``stop_when`` guards (``null_cursor``, ``repeated_cursor``, ``empty_page``,
  ``no_next``).  ``repeated_cursor`` is surfaced as ``repeated_cursor``
  so the sync layer can mark ``Degraded`` with ``no_loss`` (W3.3 / W3.5).
- Params may contain ``{{checkpoint.<op>}}`` templates resolved from the
  caller-supplied checkpoint dict.
- JSONPath subset evaluation uses :mod:`jsonpath_ng` (already a dependency).

Trust boundary: no import of :mod:`verifier` or :mod:`simulators`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from autobridge.runtime.auth import build_auth_body
from autobridge.runtime.feeds import FeedError, FeedReader, fold_resends
from autobridge.runtime.http import HttpClient  # noqa: TC001
from autobridge.runtime.pagination import (
    advance_state,
    build_request_body,
    build_request_params,
    effective_count,
    evaluate_stop,
    initial_state,
)
from autobridge.spec.models import ConnectorSpec, OperationSpec, Pagination  # noqa: TC001

try:
    from jsonpath_ng import parse as jsonpath_parse
except ImportError:  # pragma: no cover
    jsonpath_parse = None  # type: ignore[assignment]

# Template for checkpoint substitution: {{checkpoint.<key>}}
_CHECKPOINT_RE = re.compile(r"\{\{\s*checkpoint\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _jsonpath_find(data: Any, path: str) -> Any:
    """Evaluate JSONPath subset *path* against *data*.

    Returns the first match's value, or ``None`` when there is no match.
    For ``items_path`` callers that need a list, use :func:`_jsonpath_find_all`.
    """
    if jsonpath_parse is None:
        raise RuntimeError("jsonpath-ng is required for JSONPath evaluation")
    expr = jsonpath_parse(path)
    matches = expr.find(data)
    if not matches:
        return None
    # Return first match value; callers for items expect list extraction via find_all.
    return matches[0].value


def _jsonpath_find_all(data: Any, path: str) -> list[Any]:
    if jsonpath_parse is None:
        raise RuntimeError("jsonpath-ng is required for JSONPath evaluation")
    expr = jsonpath_parse(path)
    return [m.value for m in expr.find(data)]


def extract_jsonpath(data: Any, path: str) -> Any:
    """Public helper to extract a JSONPath value (first match)."""
    return _jsonpath_find(data, path)


def extract_jsonpath_list(data: Any, path: str) -> list[Any]:
    """Extract all matches for *path* (useful for items_path that yields list)."""
    return _jsonpath_find_all(data, path)


def resolve_checkpoint_templates(params: dict[str, str], checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve ``{{checkpoint.<key>}}`` templates in operation params.

    - When a value is exactly ``{{checkpoint.X}}`` it is replaced by
      ``checkpoint[X]`` (or ``""`` when missing / no checkpoint).
      When a value contains a template as substring, string substitution is
      performed.
    - Plain values pass through.
    """
    if not params:
        return {}
    return _resolve_templates_in(params, checkpoint)


def _resolve_templates_in(value: Any, checkpoint: dict[str, Any] | None) -> Any:
    """Recursively resolve checkpoint templates in nested body data."""
    cp = checkpoint or {}
    if isinstance(value, str):
        m = _CHECKPOINT_RE.fullmatch(value.strip())
        if m:
            return str(cp.get(m.group(1), ""))
        if _CHECKPOINT_RE.search(value):
            def _repl(match: re.Match[str]) -> str:
                return str(cp.get(match.group(1), ""))

            return _CHECKPOINT_RE.sub(_repl, value)
        return value
    if isinstance(value, dict):
        return {k: _resolve_templates_in(v, checkpoint) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_templates_in(v, checkpoint) for v in value]
    return value


def resolve_body_templates(body: dict[str, Any] | None, checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve checkpoint templates in a POST body template (nested)."""
    if not body:
        return {}
    resolved = _resolve_templates_in(body, checkpoint)
    assert isinstance(resolved, dict)
    return resolved


def _extract_items(response_json: dict[str, Any], items_path: str) -> list[dict[str, Any]]:
    """Extract items array via *items_path*.

    Handles both direct list result and wrapped object.
    """
    raw = _jsonpath_find(response_json, items_path)
    if raw is None:
        # Fallback: try find_all and flatten if the path itself yields list elements.
        # e.g. $.data when response is {"data": [..]} should yield the list.
        return []
    if isinstance(raw, list):
        return raw
    # If the path points to a single object that is supposed to be list, wrap.
    return [raw] if isinstance(raw, dict) else []


def _single_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract records from a single (unpaginated) response."""
    if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], list):
        return raw["data"]
    if isinstance(raw, list):
        return raw
    items = _extract_items(raw, "$")
    if not items and isinstance(raw, dict) and raw:
        items = [raw]
    return items


def _extract_next_token(response_json: dict[str, Any], next_path: str | None) -> Any:
    if next_path is None:
        return None
    return _jsonpath_find(response_json, next_path)


def _extract_total(response_json: dict[str, Any], total_path: str | None) -> int | None:
    """Extract an advertised total count for tokenless page pagination.

    Returns the total as int, or None when absent/unparseable (callers fall
    back to the normal stop guards).
    """
    if total_path is None:
        return None
    try:
        total = _jsonpath_find(response_json, total_path)
    except Exception:
        return None
    if isinstance(total, bool):
        return None
    if isinstance(total, int):
        return total if total >= 0 else None
    try:
        as_int = int(str(total))
    except (TypeError, ValueError):
        return None
    return as_int if as_int >= 0 else None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge *override* into a copy of *base* (nested dicts merge, override wins)."""
    import copy

    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@dataclass(slots=True)
class OperationResult:
    """Result of executing one operation (possibly paginated)."""

    operation: str
    records: list[dict[str, Any]] = field(default_factory=list)
    pages_fetched: int = 0
    raw_pages: list[dict[str, Any]] = field(default_factory=list)
    repeated_cursor: bool = False
    stop_reason: str | None = None
    checkpoint_value: Any | None = None


class Interpreter:
    """Executes a :class:`ConnectorSpec`'s operations via an :class:`HttpClient`.

    ``feed_reader`` serves EVENT operations (spec v3 event feeds): records
    pushed to the on-premise agent and drained through the edge relay. HTTP
    operations always use ``http``; feed operations always use the reader.
    """

    def __init__(
        self,
        spec: ConnectorSpec,
        http_client: HttpClient | None = None,
        feed_reader: FeedReader | None = None,
    ) -> None:
        self.spec = spec
        self.feed_reader = feed_reader
        if http_client is not None:
            self.http = http_client
        elif spec.source.protocol == "http":
            # Build client from spec.source
            self.http = HttpClient(
                base_url=spec.source.base_url,
                auth=spec.source.auth,
                retry=spec.source.rate_limit.retry,
            )
        else:
            # Event-driven transports never build a vendor HTTP client; HTTP
            # operations on such a spec fail loudly at execution time.
            self.http = None  # type: ignore[assignment]

    def execute_operation(
        self,
        operation_name: str,
        *,
        checkpoint: dict[str, Any] | None = None,
        max_pages: int = 1000,
        limit_override: int | None = None,
    ) -> OperationResult:
        """Execute *operation_name* with pagination, retry handled by :class:`HttpClient`.

        *checkpoint* supplies ``{{checkpoint.<op>}}`` substitutions.
        *max_pages* is a safety cap (``page_limit`` guard).
        """
        op = self.spec.operations.get(operation_name)
        if op is None:
            raise KeyError(f"unknown operation {operation_name!r}")

        if op.feed is not None:
            return self._fetch_feed(op, operation_name)

        if self.http is None:
            raise FeedError(
                f"operation {operation_name!r} needs HTTP but spec "
                f"protocol {self.spec.source.protocol!r} builds no HTTP client"
            )

        params_template = op.params or {}
        resolved_params = resolve_checkpoint_templates(params_template, checkpoint)
        resolved_body = resolve_body_templates(op.body, checkpoint)

        # body_fields auth (spec v2): secrets ride in the JSON body, never headers.
        if self.spec.source.auth.type == "body_fields":
            resolved_body = _deep_merge(resolved_body, build_auth_body(self.spec.source.auth))

        if op.method == "POST":
            if op.pagination is None:
                raw = self.http.post(op.path, params=resolved_params, json_body=resolved_body)
                items = _single_items(raw)
                return OperationResult(
                    operation=operation_name,
                    records=items,
                    pages_fetched=1,
                    raw_pages=[raw],
                    stop_reason="no_pagination",
                )
            if op.pagination.strategy != "body_cursor":
                raise ValueError(
                    f"POST operation {operation_name!r} requires body_cursor pagination, "
                    f"got {op.pagination.strategy!r}"
                )
            return self._paginated_fetch_post(
                op, operation_name, resolved_params, resolved_body, max_pages, limit_override
            )

        if op.pagination is None:
            # Single request, no pagination.
            raw = self.http.get(op.path, params=resolved_params)
            items = _single_items(raw)
            return OperationResult(
                operation=operation_name,
                records=items,
                pages_fetched=1,
                raw_pages=[raw],
                stop_reason="no_pagination",
            )

        return self._paginated_fetch(op, operation_name, resolved_params, max_pages, limit_override)

    def _fetch_feed(self, op: OperationSpec, operation_name: str) -> OperationResult:
        """Drain one event feed: pushed records, folded resends, no paging.

        Delivery is at-least-once (the peer resends until answered), so the
        feed's ``dedupe_keys`` identify one instance and repeats are folded
        before mapping — the same record arriving twice never becomes two
        canonical records. ``stop_reason`` is ``event_feed``: there is no
        cursor to advance and no page to stop on.
        """
        if op.feed is None:  # pragma: no cover - guarded by caller
            raise FeedError(f"operation {operation_name!r} has no feed declaration")
        if self.feed_reader is None:
            raise FeedError(
                f"no feed reader configured for event operation {operation_name!r} "
                f"(feed {op.feed.record}); sync under a feed-capable runner"
            )
        records = self.feed_reader.read(op.feed, operation_name)
        folded = fold_resends(records, list(op.feed.dedupe_keys or []))
        return OperationResult(
            operation=operation_name,
            records=folded,
            pages_fetched=1,
            raw_pages=[{"feed": op.feed.record, "via": op.feed.via, "count": len(folded)}],
            stop_reason="event_feed",
        )

    def _paginated_fetch(
        self,
        op: OperationSpec,
        operation_name: str,
        base_params: dict[str, Any],
        max_pages: int,
        limit_override: int | None,
    ) -> OperationResult:
        pagination: Pagination = op.pagination  # type: ignore[assignment]
        strategy = pagination.strategy
        items_path = pagination.items_path
        next_path = pagination.next_path
        total_path = pagination.total_path
        stop_when = set(pagination.stop_when or [])
        limit_value = limit_override or 50

        state = initial_state(strategy)
        all_records: list[dict[str, Any]] = []
        raw_pages: list[dict[str, Any]] = []
        repeated_cursor = False
        stop_reason: str | None = None
        pages_fetched = 0

        for _ in range(max_pages):
            page_params = build_request_params(state, pagination, base_params, limit_value)

            raw = self.http.get(op.path, params=page_params)
            raw_pages.append(raw)
            pages_fetched += 1
            state.pages_fetched = pages_fetched

            items = _extract_items(raw, items_path)
            all_records.extend(items)

            next_token = _extract_next_token(raw, next_path)

            # Tokenless page pagination (e.g. method-style APIs with
            # pageNumber/pageSize and a total count but no next token):
            # keep fetching while fewer records than the advertised total
            # have arrived. A missing/unparseable total falls through to the
            # normal guards (first page only, then implicit no_next_token).
            # Reaching the advertised total is a normal, explicit stop
            # (``total_reached``), not an implicit-stop drift signal.
            if strategy == "page" and next_token is None and total_path is not None:
                total = _extract_total(raw, total_path)
                if total is not None and len(all_records) >= total:
                    stop_reason = "total_reached"
                    break
                if total is not None and len(all_records) < total:
                    state = advance_state(state, next_token, items, limit_value)
                    continue

            should_stop, reason, is_repeated = evaluate_stop(
                state=state,
                items=items,
                next_token=next_token,
                stop_when=stop_when,  # type: ignore[arg-type]
                max_pages=max_pages,
            )
            if should_stop:
                stop_reason = reason
                if is_repeated:
                    repeated_cursor = True
                break

            # Advance pagination state for next iteration
            state = advance_state(state, next_token, items, limit_value)

        # Checkpoint value: compute max/min over checkpoint field if defined
        checkpoint_value = None
        if op.checkpoint and all_records:
            cp_field = op.checkpoint.field
            values = []
            for rec in all_records:
                v = _jsonpath_find(rec, cp_field)
                if v is not None:
                    values.append(v)
            if values:
                # Lexicographic max/min is sufficient for ISO timestamps; numeric for numbers.
                try:
                    checkpoint_value = max(values) if op.checkpoint.mode == "max" else min(values)
                except TypeError:
                    # Mixed types: stringify comparison
                    str_vals = [str(x) for x in values]
                    checkpoint_value = max(str_vals) if op.checkpoint.mode == "max" else min(str_vals)

        return OperationResult(
            operation=operation_name,
            records=all_records,
            pages_fetched=pages_fetched,
            raw_pages=raw_pages,
            repeated_cursor=repeated_cursor,
            stop_reason=stop_reason,
            checkpoint_value=checkpoint_value,
        )

    def _paginated_fetch_post(
        self,
        op: OperationSpec,
        operation_name: str,
        base_params: dict[str, Any],
        base_body: dict[str, Any],
        max_pages: int,
        limit_override: int | None,
    ) -> OperationResult:
        """Fetch all pages of a POST operation with ``body_cursor`` pagination.

        The cursor and page size travel in the JSON body; the next cursor
        comes back via ``next_path``. Stops on null cursor, repeated cursor,
        empty page, page limit — or a short page (fewer items than the
        requested count), which is the body-cursor end-of-data signal.
        """
        pagination: Pagination = op.pagination  # type: ignore[assignment]
        items_path = pagination.items_path
        next_path = pagination.next_path
        stop_when = set(pagination.stop_when or [])
        limit_value = limit_override or effective_count(pagination, 50)

        state = initial_state("body_cursor")
        all_records: list[dict[str, Any]] = []
        raw_pages: list[dict[str, Any]] = []
        repeated_cursor = False
        stop_reason: str | None = None
        pages_fetched = 0

        for _ in range(max_pages):
            page_body = build_request_body(state, pagination, base_body, limit_value)
            raw = self.http.post(op.path, params=base_params, json_body=page_body)
            raw_pages.append(raw)
            pages_fetched += 1
            state.pages_fetched = pages_fetched

            items = _extract_items(raw, items_path)
            all_records.extend(items)

            next_token = _extract_next_token(raw, next_path)

            should_stop, reason, is_repeated = evaluate_stop(
                state=state,
                items=items,
                next_token=next_token,
                stop_when=stop_when,  # type: ignore[arg-type]
                max_pages=max_pages,
            )
            if should_stop:
                stop_reason = reason
                if is_repeated:
                    repeated_cursor = True
                break

            # Short page: fewer items than requested means no more data.
            if len(items) < limit_value:
                stop_reason = "short_page"
                break

            state = advance_state(state, next_token, items, limit_value)

        checkpoint_value = self._checkpoint_value(op, all_records)

        return OperationResult(
            operation=operation_name,
            records=all_records,
            pages_fetched=pages_fetched,
            raw_pages=raw_pages,
            repeated_cursor=repeated_cursor,
            stop_reason=stop_reason,
            checkpoint_value=checkpoint_value,
        )

    @staticmethod
    def _checkpoint_value(op: OperationSpec, all_records: list[dict[str, Any]]) -> Any:
        """Compute max/min over the checkpoint field for fetched records."""
        if not op.checkpoint or not all_records:
            return None
        values = []
        for rec in all_records:
            v = _jsonpath_find(rec, op.checkpoint.field)
            if v is not None:
                values.append(v)
        if not values:
            return None
        try:
            return max(values) if op.checkpoint.mode == "max" else min(values)
        except TypeError:
            str_vals = [str(x) for x in values]
            return max(str_vals) if op.checkpoint.mode == "max" else min(str_vals)

    def execute_all(
        self,
        *,
        checkpoint: dict[str, Any] | None = None,
        max_pages: int = 1000,
    ) -> dict[str, OperationResult]:
        """Execute every operation in the spec, returning per-operation results."""
        out: dict[str, OperationResult] = {}
        for op_name in self.spec.operations:
            out[op_name] = self.execute_operation(op_name, checkpoint=checkpoint, max_pages=max_pages)
        return out


# ---------------------------------------------------------------------------
# Convenience helpers (for sync handlers that take a spec dict)
# ---------------------------------------------------------------------------


def execute_spec_dict(
    spec_dict: dict[str, Any],
    operation: str,
    http_client: HttpClient | None = None,
    *,
    checkpoint: dict[str, Any] | None = None,
    max_pages: int = 1000,
    feed_reader: FeedReader | None = None,
) -> OperationResult:
    """Execute *operation* from a raw spec dict (validates first)."""
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    interp = Interpreter(spec, http_client=http_client, feed_reader=feed_reader)
    return interp.execute_operation(operation, checkpoint=checkpoint, max_pages=max_pages)
