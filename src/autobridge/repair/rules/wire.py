"""Wire-observed evidence helpers for deterministic repair rules (REQ-027).

The detector (:mod:`autobridge.drift.signals`) attaches wire observations to
``pagination_changed`` / ``keys_changed`` signal details — the cursor leaf
seen on the raw envelope, missing/added wire keys with samples, candidate
items arrays. Those details are derived only from observed responses
(``raw_pages`` / ``records``), never from scenario ids, fault YAML or answer
keys. Rules read them through the helpers below via ``ctx.signals``.

A repair is still a JSON Patch over the closed transform set (plan §1.1);
these helpers only supply the evidence a patch is built from.
"""

from __future__ import annotations

from typing import Any


def _signal_triples(signals: list[Any]) -> list[tuple[str | None, str | None, dict[str, Any]]]:
    """Normalize signals (objects or dicts) to (kind, operation, details)."""
    out: list[tuple[str | None, str | None, dict[str, Any]]] = []
    for sig in signals or []:
        if isinstance(sig, dict):
            kind = sig.get("kind")
            operation = sig.get("operation")
            details = sig.get("details")
        else:
            kind = getattr(sig, "kind", None)
            operation = getattr(sig, "operation", None)
            details = getattr(sig, "details", None)
        out.append(
            (
                kind if isinstance(kind, str) else None,
                operation if isinstance(operation, str) else None,
                details if isinstance(details, dict) else {},
            )
        )
    return out


def wire_cursor_hints(signals: list[Any]) -> list[dict[str, Any]]:
    """Wire cursor observations: one entry per contradicting operation.

    Entry: ``{"operation", "spec_strategy", "cursor_field", "cursor_sample"}``.
    """
    hints: list[dict[str, Any]] = []
    for kind, operation, details in _signal_triples(signals):
        if kind != "pagination_changed":
            continue
        if details.get("wire_strategy_hint") != "cursor":
            continue
        cursor_field = details.get("cursor_field")
        if not isinstance(cursor_field, str) or not cursor_field:
            continue
        hints.append(
            {
                "operation": operation,
                "spec_strategy": details.get("spec_strategy"),
                "cursor_field": cursor_field,
                "cursor_sample": details.get("cursor_sample"),
            }
        )
    return hints


def wire_cursor_for_strategy(
    signals: list[Any], strategies: set[str]
) -> dict[str, str]:
    """Map operation -> observed cursor leaf for ops whose spec strategy matches.

    Only returns hints whose ``spec_strategy`` is in *strategies* (e.g.
    ``{"page"}``), so a rule fires for the strategy it repairs.
    """
    out: dict[str, str] = {}
    for hint in wire_cursor_hints(signals):
        if hint.get("spec_strategy") not in strategies:
            continue
        op = hint.get("operation")
        field = hint.get("cursor_field")
        if isinstance(op, str) and isinstance(field, str) and op not in out:
            out[op] = field
    return out


def wire_next_path_hint(signals: list[Any], operation: str) -> str | None:
    """Observed replacement ``next_path`` leaf for a cursor-family operation.

    Sources: an ``observed_leaf`` on a pagination wire signal (the spec's
    ``next_path`` reads null while the wire carries another cursor-like
    leaf). Returns a ``$.<leaf>`` JSONPath or None.
    """
    for kind, op, details in _signal_triples(signals):
        if kind != "pagination_changed" or op != operation:
            continue
        leaf = details.get("observed_leaf")
        if isinstance(leaf, str) and leaf:
            return f"$.{leaf}"
    return None


def wire_rename_hint(signals: list[Any]) -> tuple[str | None, str, str] | None:
    """Single missing + single added wire key: (entity, old_key, new_key)."""
    for kind, _op, details in _signal_triples(signals):
        if kind != "keys_changed":
            continue
        missing = details.get("missing")
        added = details.get("added")
        if (
            isinstance(missing, list)
            and len(missing) == 1
            and isinstance(missing[0], str)
            and isinstance(added, list)
            and len(added) == 1
            and isinstance(added[0], dict)
            and isinstance(added[0].get("key"), str)
        ):
            entity = details.get("entity")
            return (
                entity if isinstance(entity, str) else None,
                missing[0],
                str(added[0]["key"]),
            )
    return None


def wire_items_shift(signals: list[Any]) -> tuple[str, str] | None:
    """Single-candidate items-path shift: (operation, new_items_path).

    Only fires when the wire carries exactly one candidate array, so the new
    path is unambiguous. Ambiguous envelopes (zero or several candidates)
    return None and the drift escalates with an operator question.
    """
    for kind, operation, details in _signal_triples(signals):
        if kind != "pagination_changed":
            continue
        candidates = details.get("array_candidates")
        if (
            isinstance(candidates, list)
            and len(candidates) == 1
            and isinstance(candidates[0], str)
            and isinstance(operation, str)
        ):
            return (operation, candidates[0])
    return None
