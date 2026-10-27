"""Invariant checks on canonical records (P1.W3.4, plan §2.4).

Each check is a pure function returning a list of failed criteria.
They operate on already-mapped canonical records, not raw API payloads.

Checks:
  - unique_key: no duplicate canonical keys
  - no_loss: produced count matches expected count (compare_to JSONPath or direct count)
  - date_order: before field date ≤ after field date

Trust boundary: no import of verifier or simulators.
"""

from __future__ import annotations

import datetime
from typing import Any

from autobridge.runtime.transforms.base import _parse_iso_date_string


def _parse_date_value(value: Any) -> datetime.date | None:
    """Parse YYYY-MM-DD string or date object to date."""
    if value is None:
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, str):
        s = value.strip()
        try:
            return datetime.date.fromisoformat(s)
        except ValueError:
            # Try datetime string then date
            pure = _parse_iso_date_string(s)
            if pure is not None:
                return pure
            try:
                # datetime with time
                from autobridge.runtime.transforms.base import _parse_iso_datetime

                dt = _parse_iso_datetime(s)
                return dt.date()
            except Exception:
                return None
    return None


def check_unique_key(
    records: list[dict[str, Any]],
    key_field: str = "reservation_id",
    *,
    entity_name: str | None = None,  # noqa: ARG001
) -> list[str]:
    """Check that canonical key is unique.

    Returns list containing "unique_key" if duplicates found, else [].
    """
    seen: set[str] = set()
    for rec in records:
        k = rec.get(key_field)
        if k is None:
            # Missing key considered via other checks, not duplicate
            continue
        ks = str(k)
        if ks in seen:
            return ["unique_key"]
        seen.add(ks)
    # Also detect duplicates when key_field not present but records have same id-like field
    # Fallback: check reservation_id / charge_id generically
    return []


def check_unique_key_generic(
    records: list[dict[str, Any]],
    *,
    key_fields: list[str] | None = None,
) -> list[str]:
    """Generic unique check across candidate key fields."""
    candidates = key_fields or ["reservation_id", "charge_id", "id"]
    for field in candidates:
        if any(field in r for r in records):
            return check_unique_key(records, key_field=field)
    # Try any id-like field
    if records and records[0]:
        first_key = next(iter(records[0].keys()), None)
        if first_key:
            return check_unique_key(records, key_field=first_key)
    return []


def check_no_loss(
    produced: list[dict[str, Any]],
    expected_count: int | None = None,
    *,
    compare_to_value: Any | None = None,
    entity_name: str | None = None,  # noqa: ARG001
) -> list[str]:
    """Check that no records were lost.

    - If expected_count provided, compare len(produced) >= expected_count
    - If compare_to_value provided (extracted via compare_to JSONPath), compare
    - Otherwise, just return [] (no expectation)
    Returns ["no_loss"] if loss detected.
    """
    expected: int | None = None
    if expected_count is not None:
        expected = expected_count
    elif compare_to_value is not None:
        try:
            expected = int(compare_to_value)
        except (TypeError, ValueError):
            # Could be string count
            try:
                expected = int(str(compare_to_value).strip())
            except Exception:
                expected = None

    if expected is not None and len(produced) < expected:
        return ["no_loss"]
    return []


def check_date_order(
    records: list[dict[str, Any]],
    before_field: str,
    after_field: str,
) -> list[str]:
    """Check that before_field date <= after_field date for all records.

    Returns ["date_order"] if any violation.
    """
    for rec in records:
        before_val = rec.get(before_field)
        after_val = rec.get(after_field)
        if before_val is None or after_val is None:
            continue
        before_date = _parse_date_value(before_val)
        after_date = _parse_date_value(after_val)
        if before_date is None or after_date is None:
            continue
        if after_date < before_date:
            return ["date_order"]
    return []


def run_checks(
    records: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    *,
    expected_counts: dict[str, int] | None = None,
    compare_to_values: dict[str, Any] | None = None,
    entity_name: str | None = None,
) -> list[str]:
    """Run all checks from spec for one entity.

    Checks is list of EntityCheck dicts: {"kind": ..., "compare_to": ..., "before": ..., "after": ...}
    Returns aggregated unmet_criteria names (unique).
    """
    unmet: list[str] = []
    for chk in checks:
        kind = chk.get("kind")
        if kind == "unique_key":
            # Determine key field: use entity's key path leaf or fallback
            key_field = chk.get("key_field", "reservation_id")
            # Heuristic: entity_name FolioCharge -> charge_id
            if entity_name == "FolioCharge" and key_field == "reservation_id":
                key_field = "charge_id"
            res = check_unique_key(records, key_field=key_field, entity_name=entity_name)
            for name in res:
                if name not in unmet:
                    unmet.append(name)
        elif kind == "no_loss":
            exp = None
            comp_val = None
            if expected_counts and entity_name in expected_counts:
                exp = expected_counts[entity_name]
            if compare_to_values and entity_name in compare_to_values:
                comp_val = compare_to_values[entity_name]
            # Also try chk compare_to literal if it looks like int
            res = check_no_loss(records, expected_count=exp, compare_to_value=comp_val, entity_name=entity_name)
            for name in res:
                if name not in unmet:
                    unmet.append(name)
            # Fallback: if chk has compare_to but no value, treat as no expectation
        elif kind == "date_order":
            before = chk.get("before")
            after = chk.get("after")
            if before and after:
                res = check_date_order(records, before_field=before, after_field=after)
                for name in res:
                    if name not in unmet:
                        unmet.append(name)
        # Unknown check kind is ignored (future-proof) — but should not happen per validation
    return unmet


def evaluate_entity_checks(
    entity_name: str,
    records: list[dict[str, Any]],
    checks: list[Any],
    *,
    expected_count: int | None = None,
    compare_to_value: Any | None = None,
) -> list[str]:
    """Evaluate EntityCheck objects (pydantic models or dicts) for one entity."""
    # Normalize checks to dicts
    dicts: list[dict[str, Any]] = []
    for chk in checks:
        if isinstance(chk, dict):
            dicts.append(chk)
        elif hasattr(chk, "model_dump"):
            dicts.append(chk.model_dump())
        elif hasattr(chk, "kind"):
            d: dict[str, Any] = {"kind": chk.kind}
            if hasattr(chk, "compare_to"):
                d["compare_to"] = chk.compare_to
            if hasattr(chk, "before"):
                d["before"] = getattr(chk, "before", None)
            if hasattr(chk, "after"):
                d["after"] = getattr(chk, "after", None)
            dicts.append(d)
        else:
            dicts.append(dict(chk))

    expected_counts = {entity_name: expected_count} if expected_count is not None else None
    compare_to_values = {entity_name: compare_to_value} if compare_to_value is not None else None
    return run_checks(records, dicts, expected_counts=expected_counts, compare_to_values=compare_to_values, entity_name=entity_name)
