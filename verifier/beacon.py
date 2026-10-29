"""Beacon answer keys, reference solver and arm grading (REQ-025 item 2).

The generator (``simulators/common/faultgen.py``) produces instances; this
module — on the verifier side, where answer keys live — turns an instance
into its answer key, proves the instance is solvable, and grades arm
outcomes against it.

- :func:`answer_key_for`: counts, money totals and shape hashes only, never
  fixture rows (the verifier API returns aggregates, never rows).
- :func:`reference_solve`: given the instance parameters, reaches the
  correct outcome class. For ``repair`` it inverts the mutation and
  byte-compares against the regenerated baseline; for ``escalate`` and
  ``degraded_question`` it asserts the trigger is really present in the
  served shape; for ``no_patch`` it simulates the behaviour contract. An
  instance the reference solver cannot handle raises
  :class:`faultgen.GeneratorBug` — a generator bug (no impossible
  questions), never an engine failure.
- :func:`accepted_outcomes_for`: the outcome set a run grades against.
  Plain semantic scaling is direction-ambiguous without independent
  evidence (the ``gross_to_net`` precedent ends ``Degraded``), so a
  verified patch *or* a correct escalation both pass there; every other
  family grades against its single expected class.
- :func:`grade_arm_outcome`: pure predicate over an arm's reported
  outcome (``Complete``/``Degraded``/``Blocked`` + patch/question flags).

Trust boundary: imports ``simulators.common`` (sanctioned shared code)
and stdlib only. Never imports ``autobridge``.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

import simulators.common.faultgen as faultgen
from simulators.common.faultgen import GeneratorBug
from simulators.common.generator import generate_dataset

# ---------------------------------------------------------------------------
# Answer keys (counts and hashes only, never fixture rows)
# ---------------------------------------------------------------------------


def _money_total(records: list[dict[str, Any]], key: str) -> Decimal:
    total = Decimal("0")
    for rec in records:
        raw = rec.get(key)
        if isinstance(raw, str):
            try:
                total += Decimal(raw)
            except Exception:
                continue
    return total


def answer_key_for(instance: dict[str, Any]) -> dict[str, Any]:
    """Build the verifier-held answer key for an instance.

    ``truth`` carries only aggregates: record counts, exact money totals
    (Decimal strings) and a sha256 over the sorted served record keys —
    enough to re-derive the expected canonical shape, never fixture rows.
    """
    materialized = faultgen.materialize(instance)
    dataset = materialized["dataset"]
    reservations = dataset.get("reservations", [])
    folios = dataset.get("folios", [])
    key_set: set[str] = set()
    for rec in reservations + folios:
        key_set.update(_record_leaf_keys(rec))
    key_hash = hashlib.sha256(json.dumps(sorted(key_set), sort_keys=True).encode()).hexdigest()
    money_total = _money_total(reservations, "total_amount") + _money_total(folios, "amount")
    return {
        "instance_index": int(instance["index"]),
        "vendor": str(instance["vendor"]),
        "family": str(instance["family"]),
        "expected": str(instance["expected"]),
        "accepted": sorted(accepted_outcomes_for(instance)),
        "truth": {
            "n_reservations": len(reservations),
            "n_folios": len(folios),
            "money_total": format(money_total, "f"),
            "record_keys_hash": key_hash,
        },
    }


def _record_leaf_keys(rec: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(rec, dict):
        for key, value in rec.items():
            keys.add(str(key))
            if isinstance(value, dict):
                keys.update(_record_leaf_keys(value))
    return keys


def accepted_outcomes_for(instance: dict[str, Any]) -> set[str]:
    """Outcome classes a run accepts for this instance.

    Singular ``expected`` except for plain semantic scaling, where the
    direction (gross→net vs net→gross, units vs subunits) is ambiguous
    without independent evidence: a verifier-accepted patch *or* a correct
    escalation to one operator question both pass, mirroring the standing
    ``gross_to_net`` precedent (Degraded + question).
    """
    expected = str(instance["expected"])
    if str(instance["family"]) == "semantic" and expected == "repair":
        return {"repair", "degraded_question"}
    return {expected}


# ---------------------------------------------------------------------------
# Reference solver (item 2: no impossible questions)
# ---------------------------------------------------------------------------


def reference_solve(instance: dict[str, Any]) -> str:
    """Reach the correct outcome class from the instance parameters.

    Raises :class:`GeneratorBug` when the served shape does not support
    the expected class — that is a generator bug, never an engine failure.
    """
    materialized = faultgen.materialize(instance)
    expected = str(instance["expected"])
    family = str(instance["family"])
    params = dict(instance["params"])
    dataset = materialized["dataset"]
    behavior = materialized["behavior"]

    if expected == "repair":
        faultgen.inverse_canonicalize(instance, dataset)
        return "repair"
    if expected == "degraded_question":
        _assert_trap_present(dataset, params)
        return "degraded_question"
    if expected == "no_patch":
        _assert_recoverable(behavior, family)
        return "no_patch"
    if expected == "escalate":
        _assert_terminal(dataset, behavior, family, params)
        return "escalate"
    # Unreachable: materialize() validates the instance first, so an
    # unknown expected class raises ValueError before we get here.
    raise GeneratorBug(f"unknown expected class: {expected}")  # pragma: no cover


def _assert_trap_present(dataset: dict[str, list[dict[str, Any]]], params: dict[str, Any]) -> None:
    """The decoy field is present while the gross value stays intact."""
    decoy_field = str(params["decoy_field"])
    seen = 0
    for rec in dataset.get("reservations", []) + dataset.get("folios", []):
        if decoy_field in rec:
            seen += 1
            gross = rec.get("total_amount", rec.get("amount"))
            if not isinstance(gross, str):
                raise GeneratorBug("decoy instance lost the gross value")
            try:
                Decimal(gross)
            except Exception as exc:
                raise GeneratorBug(f"decoy instance gross unparseable: {exc}") from exc
            try:
                Decimal(str(rec[decoy_field]))
            except Exception as exc:
                raise GeneratorBug(f"decoy value unparseable: {exc}") from exc
    if seen == 0:
        raise GeneratorBug("decoy field present in no served record")


def _assert_recoverable(behavior: dict[str, Any] | None, family: str) -> None:
    """The behaviour contract always offers a no-patch path to success."""
    if behavior is None:
        raise GeneratorBug(f"{family}: no_patch needs a behaviour contract")
    if family == "transport_auth":
        kind = str(behavior.get("kind"))
        if kind == "token_expired":
            # Recoverable via refresh: 401 first, 200 after.
            status, _h, terminal = faultgen.transport_response(behavior, 1)
            if terminal or status != 401:
                raise GeneratorBug("token_expired must 401 before refresh")
            status, _h, terminal = faultgen.transport_response(behavior, 1, refreshed=True)
            if terminal or status != 200:
                raise GeneratorBug("token_expired must 200 after refresh")
            return
        for count in range(1, 12):
            status, _headers, terminal = faultgen.transport_response(behavior, count)
            if terminal:
                raise GeneratorBug("no_patch transport must never be terminal")
            if status == 200:
                return
        raise GeneratorBug("no_patch transport never returns 200")
    if family == "fias_link":
        if str(behavior.get("kind")) not in ("resync", "ack_timeout"):
            raise GeneratorBug(f"unknown fias_link kind: {behavior.get('kind')}")
        return
    raise GeneratorBug(f"no inverse-free path for family: {family}")


def _assert_terminal(
    dataset: dict[str, list[dict[str, Any]]],
    behavior: dict[str, Any] | None,
    family: str,
    params: dict[str, Any],
) -> None:
    """The escalation trigger is really present in the served shape."""
    if family == "transport_auth":
        if behavior is None:
            raise GeneratorBug("escalate transport needs a behaviour contract")
        status, _headers, terminal = faultgen.transport_response(behavior, 1)
        if not terminal or status not in (403, 404):
            raise GeneratorBug(f"escalate transport must fail terminally, got {status}")
        return
    if family == "code_list":
        unknown_code = params.get("unknown_code")
        if not unknown_code:
            raise GeneratorBug("escalate code_list needs an unknown code")
        for rec in dataset.get("reservations", []):
            if rec.get("status") == unknown_code:
                mapping = dict(params.get("mapping", {}))
                if unknown_code in mapping:
                    raise GeneratorBug("unknown code has a canonical meaning")
                return
        raise GeneratorBug("unknown code present in no served record")
    raise GeneratorBug(f"no escalation trigger for family: {family}")


# ---------------------------------------------------------------------------
# Arm grading (pure predicate over the reported arm outcome)
# ---------------------------------------------------------------------------

#: Valid SyncRun outcome names.
ARM_OUTCOMES: frozenset[str] = frozenset({"Complete", "Degraded", "Blocked"})


def grade_arm_outcome(instance: dict[str, Any], arm: dict[str, Any]) -> tuple[bool, str]:
    """Grade one arm's reported outcome against the instance's answer key.

    ``arm`` carries ``outcome`` (``Complete``/``Degraded``/``Blocked``),
    ``patch_reached_release`` and ``operator_question``. Returns
    ``(passed, reason)``. Unknown outcome names fail closed.
    """
    outcome = arm.get("outcome")
    if outcome not in ARM_OUTCOMES:
        return (False, f"unknown arm outcome: {outcome!r}")
    patched = bool(arm.get("patch_reached_release", False))
    question = bool(arm.get("operator_question", False))
    accepted = accepted_outcomes_for(instance)

    if outcome == "Complete" and patched and "repair" in accepted:
        return (True, "repair: verified patch reached a release")
    if outcome == "Complete" and not patched and "no_patch" in accepted:
        return (True, "no_patch: complete without a patch")
    if outcome == "Blocked" and not patched and "escalate" in accepted:
        return (True, "escalate: blocked with no patch attempted")
    if outcome == "Degraded" and question and not patched and "degraded_question" in accepted:
        return (True, "degraded_question: one operator question, no guessed patch")
    return (False, f"outcome {outcome} (patch={patched}, question={question}) not in {sorted(accepted)}")


def baseline_for(instance: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Regenerate the baseline dataset an instance was built from."""
    return generate_dataset(seed=int(instance["dataset_seed"]))
