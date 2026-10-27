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

from autobridge.runtime.http import HttpClient  # noqa: TC001
from autobridge.runtime.pagination import (
    advance_state,
    build_request_params,
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
    - When a value contains a template as substring, string substitution is
      performed.
    - Plain values pass through.
    """
    if not params:
        return {}
    cp = checkpoint or {}
    out: dict[str, Any] = {}
    for k, v in params.items():
        if not isinstance(v, str):
            out[k] = v
            continue
        # Exact match -> typed value from checkpoint (could be int, str, etc.)
        m = _CHECKPOINT_RE.fullmatch(v.strip())
        if m:
            key = m.group(1)
            out[k] = str(cp.get(key, ""))
            continue
        # Substring substitution: replace all occurrences.
        def _repl(match: re.Match[str]) -> str:
            key = match.group(1)
            return str(cp.get(key, ""))

        if _CHECKPOINT_RE.search(v):
            out[k] = _CHECKPOINT_RE.sub(_repl, v)
        else:
            out[k] = v
    return out


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


def _extract_next_token(response_json: dict[str, Any], next_path: str | None) -> Any:
    if next_path is None:
        return None
    return _jsonpath_find(response_json, next_path)


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
    """Executes a :class:`ConnectorSpec`'s operations via an :class:`HttpClient`."""

    def __init__(self, spec: ConnectorSpec, http_client: HttpClient | None = None) -> None:
        self.spec = spec
        if http_client is not None:
            self.http = http_client
        else:
            # Build client from spec.source
            self.http = HttpClient(
                base_url=spec.source.base_url,
                auth=spec.source.auth,
                retry=spec.source.rate_limit.retry,
            )

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

        params_template = op.params or {}
        resolved_params = resolve_checkpoint_templates(params_template, checkpoint)

        if op.pagination is None:
            # Single request, no pagination.
            raw = self.http.get(op.path, params=resolved_params)
            if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], list):
                items = raw["data"]
            elif isinstance(raw, list):
                items = raw
            else:
                items = _extract_items(raw, "$")
                if not items and isinstance(raw, dict) and raw:
                    items = [raw]
            return OperationResult(
                operation=operation_name,
                records=items,
                pages_fetched=1,
                raw_pages=[raw],
                stop_reason="no_pagination",
            )

        return self._paginated_fetch(op, operation_name, resolved_params, max_pages, limit_override)

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
    http_client: HttpClient,
    *,
    checkpoint: dict[str, Any] | None = None,
    max_pages: int = 1000,
) -> OperationResult:
    """Execute *operation* from a raw spec dict (validates first)."""
    from autobridge.spec.models import load_spec_from_dict

    spec = load_spec_from_dict(spec_dict)
    interp = Interpreter(spec, http_client=http_client)
    return interp.execute_operation(operation, checkpoint=checkpoint, max_pages=max_pages)
