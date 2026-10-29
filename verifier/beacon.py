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


# ---------------------------------------------------------------------------
# Produced-records judgment (POST /beacon/records)
# ---------------------------------------------------------------------------


def judge_produced_records(
    instance: dict[str, Any],
    produced: dict[str, Any],
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Judge arm-produced canonical records against the instance answer key.

    Returns ``{"verdict", "score", "failed_checks"}`` with bounded check
    names and counts only (aggregates, never fixture rows). The records do
    the real judging (canonical set, no loss/duplicates, exact money,
    property-local business dates, canonical status vocabulary); when a
    candidate spec is supplied and the instance expects a repair, the spec
    must additionally cover the mutated contract (a correct spec always
    names what it reads, so coverage never false-fails a genuine repair).
    Fail closed: malformed records, records for an ``escalate`` instance,
    or an uncovered contract all fail.
    """
    from verifier.checks import (
        check_business_date,
        check_money_exact,
        check_no_duplicates,
        check_no_loss,
        check_record_set_by_key,
        check_status_mapping,
    )

    for key in ("reservations", "folios"):
        recs = produced.get(key, [])
        if not isinstance(recs, list) or any(not isinstance(r, dict) for r in recs):
            raise ValueError(f"produced {key} must be a list of records")
    expected = str(instance["expected"])
    non_empty = bool(produced.get("reservations")) or bool(produced.get("folios"))
    if expected == "escalate" and non_empty:
        return {
            "verdict": "fail",
            "score": 0.0,
            "failed_checks": [{"name": "records_for_escalate", "count": 1}],
        }
    baseline = baseline_for(instance)
    props = baseline.get("properties") or []
    fails: list[dict[str, Any]] = []
    fails.extend(check_record_set_by_key(baseline, produced))  # type: ignore[arg-type]
    fails.extend(check_no_duplicates(produced))  # type: ignore[arg-type]
    fails.extend(check_no_loss(baseline, produced))  # type: ignore[arg-type]
    fails.extend(check_money_exact(baseline, produced))  # type: ignore[arg-type]
    fails.extend(check_business_date(baseline, produced, props))  # type: ignore[arg-type]
    fails.extend(check_status_mapping(produced))  # type: ignore[arg-type]
    fails.extend(_check_canonical_field_coverage(baseline, produced))
    if spec is not None and expected == "repair":
        fails.extend(check_contract_coverage(instance, spec))

    by_name: dict[str, dict[str, Any]] = {}
    for entry in fails:
        name = str(entry.get("name", ""))
        if name not in by_name or int(entry.get("count", 0)) > int(by_name[name].get("count", 0)):
            by_name[name] = entry
    failed = sorted(by_name.values(), key=lambda e: str(e.get("name")))
    score = 1.0 if not failed else round(max(0.0, (8 - len(failed)) / 8), 3)
    return {"verdict": "pass" if not failed else "fail", "score": score, "failed_checks": failed}


def _check_canonical_field_coverage(
    expected: dict[str, list[dict[str, Any]]], produced: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every canonical top-level field must be present in the produced record.

    An unfixed rename serves the new name (canonical missing), an unfixed
    nesting serves a single parent wrapper (canonicals missing) — both fail
    here with the bounded ``field_missing`` name. Top-level only: nesting
    is a canonical-shape breach, not a nested find. Extras are allowed.
    """
    fails: list[dict[str, Any]] = []
    for label, key in (("reservations", "reservation_id"), ("folios", "charge_id")):
        produced_by_id = {
            str(r.get(key)) for r in produced.get(label, []) if isinstance(r, dict)
        }
        missing = 0
        for rec in expected.get(label, []):
            if str(rec.get(key)) not in produced_by_id:
                continue  # counted by record_set_mismatch, not here
            prod = next(
                (r for r in produced.get(label, []) if isinstance(r, dict) and str(r.get(key)) == str(rec.get(key))),
                {},
            )
            absent = [k for k in rec if k not in prod]
            missing += len(absent)
        if missing:
            fails.append({"name": "field_missing", "count": missing})
    return fails


def check_contract_coverage(instance: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The candidate spec must name what the mutated contract serves.

    A correct repair reads the mutated shape, so its field paths, enum
    mappings and pagination declarations necessarily mention the new
    tokens; a spec that never names them cannot have repaired the drift.
    Records remain the decisive judge — coverage only adds failures.
    """
    family = str(instance["family"])
    params = dict(instance["params"])
    text = json.dumps(spec, sort_keys=True, default=str)
    missing: list[str] = []
    if family == "rename":
        missing = [t for t in [str(params.get("new_name", ""))] if t and t not in text]
    elif family == "nesting":
        parents = [str(p) for p in params.get("parents", [])]
        missing = [t for t in parents if t and t not in text]
    elif family == "code_list":
        codes = [str(c) for c in params.get("new_codes", [])]
        missing = [t for t in codes if t and t not in text]
    elif family == "pagination":
        missing = _pagination_tokens_missing(params, spec, text)
    elif family in ("semantic", "dates", "transport_auth", "fias_layout", "fias_link"):
        return []
    else:
        return [{"name": "beacon_contract_uncovered", "count": 1}]
    if missing:
        return [{"name": "beacon_contract_uncovered", "count": len(missing)}]
    return []


def _pagination_tokens_missing(
    params: dict[str, Any], spec: dict[str, Any], text: str
) -> list[str]:
    """Contract tokens absent from the candidate spec (strategy-aware)."""
    strategy = str(params.get("strategy", ""))
    if _spec_strategy(spec) != strategy:
        return [f"strategy:{strategy}"]
    if strategy == "offset":
        tokens = [str(params.get("offset_param", "")), str(params.get("limit_param", ""))]
    elif strategy == "page":
        tokens = [str(params.get("page_param", "")), str(params.get("per_page_param", ""))]
    elif strategy in ("cursor", "body_cursor"):
        tokens = [str(params.get("cursor_param", "cursor")), str(params.get("limit_param", "limit"))]
        for path in list(params.get("next_path", []) or []) + list(params.get("items_path", []) or []):
            leaf = str(path[-1]) if isinstance(path, list) and path else str(path).split(".")[-1].strip("[]")
            if leaf:
                tokens.append(leaf)
        if strategy == "body_cursor":
            tokens.append(str(params.get("cursor_leaf", "")))
            tokens.append(str(params.get("page_size_param", "")))
    else:
        return [f"strategy:{strategy}"]
    return [t for t in tokens if t and t not in text]


def _spec_strategy(spec: dict[str, Any] | None) -> str | None:
    """Pagination strategy declared by the candidate spec's first operation."""
    if not isinstance(spec, dict):
        return None
    try:
        ops = spec.get("operations", {})
        if not isinstance(ops, dict):
            return None
        for op in ops.values():
            if not isinstance(op, dict):
                continue
            pag = op.get("pagination")
            if isinstance(pag, dict) and isinstance(pag.get("strategy"), str):
                return str(pag["strategy"])
    except Exception:
        return None
    return None
