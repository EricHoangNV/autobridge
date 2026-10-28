"""Link-layer delivery checks for the FIAS on-premise family (REQ-022 D).

The HTTP oracle (``verifier/checks.py``) cannot verify framed-TCP link
guarantees, so it fails those scenarios closed with the catalog's
link-layer check name. The *real* grading lives here, over plain data the
e2e collects from the agent store, the relay and the control API:

* delivery: expected vs landed idempotency keys (no loss, no duplicates),
  amounts exact as :class:`Decimal`;
* layout: the spec's mapped wire ids vs the handshake-announced layout;
* commands: the terminal outcome the cloud was told, and whether the
  frame ever went to the PABX.

Every function returns a list of ``FailedCheck`` dicts (``name`` + bounded
``count`` + short ``details``) — aggregate only, never record rows.
An empty list means the guarantee holds.

This module never imports ``autobridge`` (trust boundary); it may use
``simulators/common`` like the rest of the verifier.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _failed(name: str, count: int, details: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "count": count}
    if details is not None:
        entry["details"] = details
    return entry


# ---------------------------------------------------------------------------
# Delivery (postings landed exactly once)
# ---------------------------------------------------------------------------


def check_delivery_no_loss(expected_keys: list[str], produced_keys: list[str]) -> list[dict[str, Any]]:
    """Every expected idempotency key landed at least once (``no_loss``)."""
    missing = sorted(set(expected_keys) - set(produced_keys))
    if not missing:
        return []
    return [_failed("no_loss", len(missing), f"missing keys: {missing[:5]}")]


def check_delivery_no_duplicates(produced_keys: list[str]) -> list[dict[str, Any]]:
    """No idempotency key landed twice (``duplicate_records``)."""
    seen: set[str] = set()
    dupes: set[str] = set()
    for key in produced_keys:
        if key in seen:
            dupes.add(key)
        seen.add(key)
    if not dupes:
        return []
    return [_failed("duplicate_records", len(dupes), f"duplicated keys: {sorted(dupes)[:5]}")]


def check_delivery_money_exact(
    expected_amounts: dict[str, str],
    produced_amounts: dict[str, str],
) -> list[dict[str, Any]]:
    """Landed amounts equal the expected minor-unit values, Decimal-exact."""
    mismatched: list[str] = []
    for key, want in expected_amounts.items():
        got = produced_amounts.get(key)
        if got is None:
            continue  # absence is no_loss's job, not money's
        try:
            if Decimal(str(got)) != Decimal(str(want)):
                mismatched.append(key)
        except (InvalidOperation, ValueError, ArithmeticError):
            mismatched.append(key)
    if not mismatched:
        return []
    return [_failed("money_mismatch", len(mismatched), f"amount mismatch: {mismatched[:5]}")]


# ---------------------------------------------------------------------------
# Layout (announced vs mapped wire ids)
# ---------------------------------------------------------------------------


def check_layout_drift(mapped_wire_ids: list[str], announced_ids: list[str]) -> list[dict[str, Any]]:
    """The handshake-announced layout covers every mapped wire id.

    A mapped id the peer no longer announces breaks the mapping (the D
    layout-change scenario); an announced id nobody maps is informational
    and rides in the details.
    """
    missing = sorted(set(mapped_wire_ids) - set(announced_ids))
    if not missing:
        return []
    added = sorted(set(announced_ids) - set(mapped_wire_ids))
    return [
        _failed(
            "layout_drift",
            len(missing),
            f"mapped ids missing from announcement: {missing}; unmapped announced: {added}",
        )
    ]


# ---------------------------------------------------------------------------
# Commands (terminal outcome reported to the cloud)
# ---------------------------------------------------------------------------


def check_command_outcome(
    reported: list[str],
    expected_outcome: str,
    sent_to_pabx: bool,
    may_send: bool,
) -> list[dict[str, Any]]:
    """The cloud heard exactly one terminal outcome, and the send happened
    (or not) as the scenario requires.

    * *reported*: terminal outcomes recorded for the request id, in order.
    * *expected_outcome*: the one outcome the scenario requires
      (``ack_timeout``, ``expired``, ``sent``, ``failed``).
    * *sent_to_pabx*: whether a frame for the command went out on the link.
    * *may_send*: whether the scenario permits the send (stale commands
      must never go out; lost sessions may retry on the new link).
    """
    if len(reported) != 1 or reported[0] != expected_outcome:
        return [
            _failed(
                "command_outcome_mismatch",
                1,
                f"reported {reported!r}, expected exactly [{expected_outcome!r}]",
            )
        ]
    if sent_to_pabx and not may_send:
        return [_failed("command_outcome_mismatch", 1, "command reached the PABX but must not have")]
    if not sent_to_pabx and may_send and expected_outcome == "sent":
        return [_failed("command_outcome_mismatch", 1, "command never reached the PABX")]
    return []


# ---------------------------------------------------------------------------
# Dispatcher (one entry point for the FIAS e2e)
# ---------------------------------------------------------------------------

#: Scenarios graded as delivery guarantees (postings in, charges out).
_DELIVERY_SCENARIOS = frozenset(
    {
        "fias_link_resync",
        "agent_restart_inflight",
        "cloud_slow",
        "fias_duplicate_records",
        "session_lost_with_command",
    }
)


def grade_fias_scenario(scenario_id: str, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Grade one FIAS scenario from caller-collected *evidence*.

    Evidence keys (all optional; absent keys skip their check):

    * ``expected_keys`` / ``produced_keys`` — idempotency-key lists;
    * ``expected_amounts`` / ``produced_amounts`` — key → minor-unit string;
    * ``mapped_wire_ids`` / ``announced_ids`` — layout comparison;
    * ``reported_outcomes`` / ``expected_outcome`` / ``sent_to_pabx`` /
      ``may_send`` — command terminal-outcome check.

    Unknown scenario ids raise ``ValueError`` (never guessed).
    """
    from simulators.common.faults import is_known_scenario  # noqa: PLC0415 - sanctioned import

    if not is_known_scenario(scenario_id):
        raise ValueError(f"unknown scenario: {scenario_id}")
    failed: list[dict[str, Any]] = []
    if "expected_keys" in evidence or "produced_keys" in evidence:
        failed.extend(
            check_delivery_no_loss(
                list(evidence.get("expected_keys") or []),
                list(evidence.get("produced_keys") or []),
            )
        )
        failed.extend(check_delivery_no_duplicates(list(evidence.get("produced_keys") or [])))
    if "expected_amounts" in evidence or "produced_amounts" in evidence:
        failed.extend(
            check_delivery_money_exact(
                dict(evidence.get("expected_amounts") or {}),
                dict(evidence.get("produced_amounts") or {}),
            )
        )
    if "mapped_wire_ids" in evidence or "announced_ids" in evidence:
        failed.extend(
            check_layout_drift(
                list(evidence.get("mapped_wire_ids") or []),
                list(evidence.get("announced_ids") or []),
            )
        )
    if "expected_outcome" in evidence:
        failed.extend(
            check_command_outcome(
                list(evidence.get("reported_outcomes") or []),
                str(evidence["expected_outcome"]),
                bool(evidence.get("sent_to_pabx", False)),
                bool(evidence.get("may_send", True)),
            )
        )
    return failed
