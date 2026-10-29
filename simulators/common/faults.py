"""Fault switchboard — named mutations for simulator scenarios (P1.W2.2).

Each scenario id corresponds to a drift class (plan §2.8). The switchboard
applies deterministic mutations to the seeded dataset and/or to API
behaviour (pagination, auth, rate limiting).

The mutations are intentionally simple and data-only — they transform
records or flip a behaviour flag that the public API checks. No code
generation, no expressions.

Scenario list (plan §2.8 + REQ-019 page_to_cursor):
  baseline, rename_guest_id, offset_to_cursor, page_to_cursor,
  cursor_repeat_token, rate_limit_429, transient_503, token_expired,
  status_vocab_change, gross_to_net, tz_boundary_shift,
  duplicate_late_records, endpoint_removed, invalid_patch_trap,
  meridian_state_renamed, meridian_cursor_renamed

FIAS on-premise family (REQ-022 D, ``transport: fias_tcp`` — link/agent
faults, no HTTP dataset mutation):
  fias_record_layout_change, fias_link_resync, fias_ack_timeout,
  agent_restart_inflight, cloud_slow, fias_duplicate_records,
  fias_oversize_frame, fias_record_flood, session_lost_with_command,
  stale_command
"""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from typing import Any

# ---------------------------------------------------------------------------
# Catalog metadata (used by verifier and docs; tags are dev|heldout + fixable|escalate)
# ---------------------------------------------------------------------------

SCENARIO_CATALOG: list[dict[str, str]] = [
    {"id": "baseline", "drift_class": "none", "fixable": "fixable", "visibility": "dev"},
    {"id": "rename_guest_id", "drift_class": "SCHEMA_RENAME", "fixable": "fixable", "visibility": "dev"},
    {"id": "offset_to_cursor", "drift_class": "PAGINATION_CHANGE", "fixable": "fixable", "visibility": "dev"},
    {"id": "page_to_cursor", "drift_class": "PAGINATION_CHANGE", "fixable": "fixable", "visibility": "dev"},
    {"id": "cursor_repeat_token", "drift_class": "PAGINATION_BUG", "fixable": "fixable", "visibility": "dev"},
    {"id": "rate_limit_429", "drift_class": "RATE_LIMIT", "fixable": "fixable", "visibility": "dev"},
    {"id": "transient_503", "drift_class": "TRANSIENT_ERROR", "fixable": "fixable", "visibility": "dev"},
    {"id": "token_expired", "drift_class": "AUTH_EXPIRED", "fixable": "fixable", "visibility": "dev"},
    {"id": "status_vocab_change", "drift_class": "STATUS_VOCAB", "fixable": "fixable", "visibility": "dev"},
    {"id": "gross_to_net", "drift_class": "SEMANTIC_VALUE_DRIFT", "fixable": "fixable", "visibility": "dev"},
    {"id": "tz_boundary_shift", "drift_class": "SEMANTIC_VALUE_DRIFT", "fixable": "fixable", "visibility": "heldout"},
    {"id": "duplicate_late_records", "drift_class": "DUPLICATE_DELIVERY", "fixable": "fixable", "visibility": "dev"},
    {"id": "endpoint_removed", "drift_class": "CAPABILITY_REMOVED", "fixable": "escalate", "visibility": "heldout"},
    {"id": "token_revoked", "drift_class": "AUTH_REVOKED", "fixable": "escalate", "visibility": "heldout"},
    {"id": "invalid_patch_trap", "drift_class": "SEMANTIC_VALUE_DRIFT", "fixable": "escalate", "visibility": "heldout"},
  {"id": "meridian_state_renamed", "drift_class": "SCHEMA_RENAME", "fixable": "fixable", "visibility": "dev"},
  {"id": "meridian_cursor_renamed", "drift_class": "PAGINATION_CHANGE", "fixable": "fixable", "visibility": "dev"},
  # REQ-022 D — FIAS on-premise family. ``transport: fias_tcp`` marks
  # link/agent faults: they carry no HTTP dataset mutation (the wire is
  # framed TCP, not REST) and are graded by the link-layer checks in
  # ``verifier/fias_checks.py`` plus the FIAS e2e file — never by the HTTP
  # oracle. ``failed_check`` names the link-layer guarantee so /verify can
  # fail closed with the exact catalog name (it cannot verify the link).
  {"id": "fias_record_layout_change", "drift_class": "SCHEMA_RENAME", "fixable": "fixable", "visibility": "dev", "transport": "fias_tcp", "failed_check": "fias_record_layout_change"},
  {"id": "fias_link_resync", "drift_class": "LINK_RESYNC", "fixable": "fixable", "visibility": "dev", "transport": "fias_tcp", "failed_check": "fias_link_resync"},
  {"id": "fias_ack_timeout", "drift_class": "ACK_TIMEOUT", "fixable": "fixable", "visibility": "dev", "transport": "fias_tcp", "failed_check": "fias_ack_timeout"},
  {"id": "agent_restart_inflight", "drift_class": "AGENT_RESTART", "fixable": "fixable", "visibility": "dev", "transport": "fias_tcp", "failed_check": "agent_restart_inflight"},
  {"id": "cloud_slow", "drift_class": "CLOUD_SLOW", "fixable": "fixable", "visibility": "dev", "transport": "fias_tcp", "failed_check": "cloud_slow"},
  {"id": "fias_duplicate_records", "drift_class": "DUPLICATE_DELIVERY", "fixable": "fixable", "visibility": "dev", "transport": "fias_tcp", "failed_check": "fias_duplicate_records"},
  {"id": "fias_oversize_frame", "drift_class": "PROTOCOL_VIOLATION", "fixable": "fixable", "visibility": "dev", "transport": "fias_tcp", "failed_check": "fias_oversize_frame"},
  {"id": "fias_record_flood", "drift_class": "PROTOCOL_VIOLATION", "fixable": "fixable", "visibility": "dev", "transport": "fias_tcp", "failed_check": "fias_record_flood"},
  {"id": "session_lost_with_command", "drift_class": "SESSION_LOST", "fixable": "fixable", "visibility": "dev", "transport": "fias_tcp", "failed_check": "session_lost_with_command"},
  {"id": "stale_command", "drift_class": "STALE_COMMAND", "fixable": "fixable", "visibility": "dev", "transport": "fias_tcp", "failed_check": "stale_command"},
]

KNOWN_SCENARIO_IDS: set[str] = {s["id"] for s in SCENARIO_CATALOG}

# Status vocab mapping for status_vocab_change (opaque codes)
STATUS_VOCAB_MAP: dict[str, str] = {
    "confirmed": "active",
    "checked_in": "in_house",
    "checked_out": "departed",
    "cancelled": "void",
    "no_show": "noshow",
}
REVERSE_STATUS_VOCAB_MAP: dict[str, str] = {v: k for k, v in STATUS_VOCAB_MAP.items()}

# Gross -> net factor (e.g. 10% tax removed)
GROSS_TO_NET_FACTOR = Decimal("0.9")

# TZ boundary shift: cutoff hour changes from 4 to 2 (business date shifts)
TZ_SHIFT_CUTOFF = 2


def is_known_scenario(scenario_id: str) -> bool:
    return scenario_id in KNOWN_SCENARIO_IDS


def transport_for_scenario(scenario_id: str) -> str:
    """Transport carrying the scenario: ``"http"`` (default) or ``"fias_tcp"``.

    The FIAS on-premise family (REQ-022 D) speaks framed TCP, not REST, so
    HTTP dataset/behaviour flags never apply to it; unknown ids raise.
    """
    for entry in SCENARIO_CATALOG:
        if entry["id"] == scenario_id:
            return str(entry.get("transport", "http"))
    raise ValueError(f"unknown scenario: {scenario_id}")


def failed_check_for_scenario(scenario_id: str) -> str | None:
    """Link-layer check name for FIAS-transport scenarios, else None.

    The HTTP oracle fails closed with this name for ``fias_tcp`` scenarios
    (it cannot verify a link guarantee); the FIAS e2e grades the real thing.
    """
    for entry in SCENARIO_CATALOG:
        if entry["id"] == scenario_id:
            failed_check = entry.get("failed_check")
            return str(failed_check) if failed_check is not None else None
    raise ValueError(f"unknown scenario: {scenario_id}")


# ---------------------------------------------------------------------------
# Record-level mutations (dataset transforms)
# ---------------------------------------------------------------------------


def apply_record_mutations(
    records: list[dict[str, Any]],
    scenario_id: str,
    record_type: str = "reservation",
) -> list[dict[str, Any]]:
    """Apply scenario's record-level mutation to a list of records.

    Returns a new list (deep copies). Unknown scenario_id raises ValueError.
    ``record_type`` is ``reservation`` or ``folio`` — some mutations are
    type-specific.
    """
    if scenario_id == "baseline":
        return [copy.deepcopy(r) for r in records]
    if scenario_id not in KNOWN_SCENARIO_IDS:
        raise ValueError(f"unknown scenario: {scenario_id}")

    out: list[dict[str, Any]] = []
    for rec in records:
        mutated = copy.deepcopy(rec)
        if scenario_id == "rename_guest_id" and record_type == "reservation":
            # Rename guest_name -> guest_identifier (or guestId)
            if "guest_name" in mutated:
                mutated["guest_identifier"] = mutated.pop("guest_name")
        elif scenario_id == "status_vocab_change" and record_type == "reservation":
            status = mutated.get("status")
            if isinstance(status, str) and status in STATUS_VOCAB_MAP:
                mutated["status"] = STATUS_VOCAB_MAP[status]
        elif scenario_id == "gross_to_net" and record_type == "reservation":
            # Convert total_amount from gross to net (multiply by 0.9)
            amt_str = mutated.get("total_amount")
            if isinstance(amt_str, str):
                try:
                    amt = Decimal(amt_str)
                    net = (amt * GROSS_TO_NET_FACTOR).quantize(Decimal("0.01"))
                    mutated["total_amount"] = format(net, ".2f")
                except Exception:
                    pass
        elif scenario_id == "gross_to_net" and record_type == "folio":
            amt_str = mutated.get("amount")
            if isinstance(amt_str, str):
                try:
                    amt = Decimal(amt_str)
                    net = (amt * GROSS_TO_NET_FACTOR).quantize(Decimal("0.01"))
                    mutated["amount"] = format(net, ".2f")
                except Exception:
                    pass
        elif scenario_id == "tz_boundary_shift" and record_type == "folio":
            # Business date cutoff shifted from 04:00 -> 02:00: late postings
            # (posted_at 00:00-03:59 local) should count to prior business date
            # with cutoff 4, but now API computes with cutoff 2, so some
            # business_dates are off by +1 day. We simulate by deterministically
            # shifting business_date forward for ~30% of records (hash-based).
            try:
                # Deterministic per-record selection via charge_id hash
                cid = str(mutated.get("charge_id", ""))
                # Simple hash: sum of char codes mod 10 < 3 => 30%
                h = sum(ord(c) for c in cid) % 10
                if h < 3 and mutated.get("business_date"):
                    import datetime as _dt

                    bdate_str = str(mutated["business_date"])
                    try:
                        bdate = _dt.date.fromisoformat(bdate_str)
                        mutated["business_date"] = (bdate + _dt.timedelta(days=1)).isoformat()
                    except Exception:
                        pass
            except Exception:
                pass
        elif scenario_id == "duplicate_late_records" and record_type in ("reservation", "folio"):
            # Duplicates are handled at pagination/delivery level, not record mutation.
            pass
        elif scenario_id == "invalid_patch_trap" and record_type == "reservation":
            # Trap: gross_to_net-like but with net field name changed
            # The plausible-but-wrong patch would map net -> total_amount incorrectly.
            # We keep total_amount as gross but add a decoy field net_amount.
            amt_str = mutated.get("total_amount")
            if isinstance(amt_str, str):
                try:
                    amt = Decimal(amt_str)
                    net = (amt * GROSS_TO_NET_FACTOR).quantize(Decimal("0.01"))
                    mutated["net_amount"] = format(net, ".2f")
                    # Keep total_amount as gross (so naive net->total_amount patch is wrong)
                except Exception:
                    pass
        # Other scenarios don't mutate records directly (they affect pagination/auth)
        out.append(mutated)

    # For duplicate_late_records, inject duplicates at list level
    if scenario_id == "duplicate_late_records":
        # Duplicate first 20% of records (deterministic)
        n_dup = max(1, len(out) // 5) if out else 0
        dupes = [copy.deepcopy(r) for r in out[:n_dup]]
        # Insert duplicates at end (simulates at-least-once)
        out = out + dupes

    return out


def apply_dataset_mutations(
    dataset: dict[str, list[dict[str, Any]]],
    scenario_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Apply scenario mutations to an entire dataset (properties + reservations + folios)."""
    if scenario_id not in KNOWN_SCENARIO_IDS:
        raise ValueError(f"unknown scenario: {scenario_id}")
    # Properties are never mutated (they define timezone/currency)
    mutated: dict[str, list[dict[str, Any]]] = {
        "properties": [copy.deepcopy(p) for p in dataset.get("properties", [])],
        "reservations": apply_record_mutations(dataset.get("reservations", []), scenario_id, "reservation"),
        "folios": apply_record_mutations(dataset.get("folios", []), scenario_id, "folio"),
    }
    return mutated


# ---------------------------------------------------------------------------
# Behaviour flags (API-level mutations)
# ---------------------------------------------------------------------------


def _active_beacon_behavior() -> dict[str, Any] | None:
    """Active beacon behaviour contract, or None (REQ-025).

    Function-level import: ``beacon_serve`` imports ``faultgen`` + ``state``
    only, so direct import here is cycle-free, but the lazy form keeps the
    hot path import-light for unit tests that never set a beacon.
    """
    from simulators.common import beacon_serve as _beacon

    instance = _beacon.active_instance()
    if instance is None:
        return None
    import simulators.common.faultgen as _fg

    return _fg.behavior_for_instance(instance)


def is_pagination_mutated(scenario_id: str) -> bool:
    """True if scenario changes pagination shape (offset/page ↔ cursor).

    Includes Meridian's Limitation cursor contract change (body_cursor leaf
    rename): the request strategy is unchanged but the fetch shape breaks.
    A REQ-025 beacon pagination instance also counts as mutated.
    """
    behavior = _active_beacon_behavior()
    if behavior is not None and behavior.get("family") == "pagination":
        return True
    return scenario_id in (
        "offset_to_cursor",
        "page_to_cursor",
        "cursor_repeat_token",
        "meridian_cursor_renamed",
    )


def pagination_mode_for_scenario(scenario_id: str, default: str = "offset") -> str:
    """Return pagination mode for scenario: 'offset', 'page', 'cursor' or 'body_cursor'."""
    behavior = _active_beacon_behavior()
    if behavior is not None and behavior.get("family") == "pagination":
        strategy = str(behavior.get("strategy", ""))
        if strategy in ("offset", "page", "cursor", "body_cursor"):
            return strategy
    if scenario_id in ("offset_to_cursor", "page_to_cursor"):
        return "cursor"
    if scenario_id == "cursor_repeat_token":
        return "cursor"
    if scenario_id in ("meridian_state_renamed", "meridian_cursor_renamed"):
        return "body_cursor"
    return default


def should_repeat_cursor(scenario_id: str) -> bool:
    """True if cursor pagination should repeat a token (bug)."""
    return scenario_id == "cursor_repeat_token"


def is_rate_limited(scenario_id: str, request_count: int) -> bool:
    """True if request should be rate-limited (429) for this scenario."""
    behavior = _active_beacon_behavior()
    if behavior is not None and behavior.get("family") == "transport_auth":
        import simulators.common.faultgen as _fg

        try:
            status, _, _ = _fg.transport_response(behavior, request_count)
        except (ValueError, KeyError, TypeError):
            return False
        return status == 429
    if scenario_id == "rate_limit_429":
        # Every 3rd request is rate limited
        return request_count % 3 == 0 and request_count > 0
    return False


def is_transient_error(scenario_id: str, request_count: int) -> bool:
    """True if request should return 503 transient error."""
    behavior = _active_beacon_behavior()
    if behavior is not None and behavior.get("family") == "transport_auth":
        import simulators.common.faultgen as _fg

        try:
            status, _, _ = _fg.transport_response(behavior, request_count)
        except (ValueError, KeyError, TypeError):
            return False
        return status == 503
    if scenario_id == "transient_503":
        # Every 4th request fails transiently
        return request_count % 4 == 0 and request_count > 0
    return False


def is_auth_expired(scenario_id: str) -> bool:
    behavior = _active_beacon_behavior()
    if behavior is not None and behavior.get("family") == "transport_auth":
        return str(behavior.get("kind")) == "token_expired"
    return scenario_id == "token_expired"


def is_auth_revoked(scenario_id: str) -> bool:
    behavior = _active_beacon_behavior()
    if behavior is not None and behavior.get("family") == "transport_auth":
        return str(behavior.get("kind")) == "token_revoked"
    return scenario_id == "token_revoked"


def is_endpoint_removed(scenario_id: str) -> bool:
    behavior = _active_beacon_behavior()
    if behavior is not None and behavior.get("family") == "transport_auth":
        return str(behavior.get("kind")) == "endpoint_removed"
    return scenario_id == "endpoint_removed"


def is_tz_shift_active(scenario_id: str) -> bool:
    return scenario_id == "tz_boundary_shift"


def is_invalid_patch_trap(scenario_id: str) -> bool:
    return scenario_id == "invalid_patch_trap"


# ---------------------------------------------------------------------------
# Drift class helpers (for verifier)
# ---------------------------------------------------------------------------


def drift_class_for_scenario(scenario_id: str) -> str:
    for entry in SCENARIO_CATALOG:
        if entry["id"] == scenario_id:
            return entry["drift_class"]
    raise ValueError(f"unknown scenario: {scenario_id}")


def is_fixable(scenario_id: str) -> bool:
    for entry in SCENARIO_CATALOG:
        if entry["id"] == scenario_id:
            return entry["fixable"] == "fixable"
    raise ValueError(f"unknown scenario: {scenario_id}")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_SCENARIO_ID_RE = re.compile(r"^[a-z0-9_]+$")


def validate_scenario_id(scenario_id: str) -> None:
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError("scenario_id must be non-empty string")
    if not _SCENARIO_ID_RE.match(scenario_id):
        raise ValueError(f"scenario_id contains invalid characters: {scenario_id}")
    if scenario_id not in KNOWN_SCENARIO_IDS:
        raise ValueError(f"unknown scenario: {scenario_id}")
