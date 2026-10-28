"""Scenario catalog loader — YAML files under simulators/scenarios/*.yaml (P1.W2.5).

Each file is a single scenario descriptor tagged ``dev|heldout`` and
``fixable|escalate``. The loader validates structure and cross-checks
against :pymod:`simulators.common.faults` SCENARIO_CATALOG so the two
sources never drift.

Public API:
    load_scenario(id) -> Scenario
    load_all_scenarios() -> list[Scenario]
    catalog_check_consistency() -> None  (raises on mismatch)
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any

import yaml

_SCENARIOS_DIR = pathlib.Path(__file__).parent

_ALLOWED_DRIFT_CLASSES = frozenset(
    {
        "none",
        "FIELD_RENAME",
        "PAGINATION_CHANGE",
        "PAGINATION_BUG",
        "RATE_LIMIT",
        "TRANSIENT_ERROR",
        "AUTH_EXPIRED",
        "AUTH_REVOKED",
        "STATUS_VOCAB",
        "SEMANTIC_VALUE_DRIFT",
        "DUPLICATE_DELIVERY",
        "CAPABILITY_REMOVED",
    }
)
_ALLOWED_FIXABLE = frozenset({"fixable", "escalate"})
_ALLOWED_VISIBILITY = frozenset({"dev", "heldout"})
_ALLOWED_VENDOR = frozenset({"both", "atlas", "harbor", "meridian"})


@dataclass(frozen=True)
class Scenario:
    """Typed view of a scenario YAML file."""

    id: str
    drift_class: str
    fixable: str
    visibility: str
    vendor: str
    seed: int
    description: str
    expected: dict[str, Any]
    raw: dict[str, Any]


def _validate_scenario_dict(data: dict[str, Any], source: str) -> None:
    if not isinstance(data, dict):
        raise ValueError(f"{source}: top-level must be a mapping")
    for key in ("id", "drift_class", "fixable", "visibility", "vendor", "seed", "description", "expected"):
        if key not in data:
            raise ValueError(f"{source}: missing required key '{key}'")
    if data["drift_class"] not in _ALLOWED_DRIFT_CLASSES:
        raise ValueError(f"{source}: unknown drift_class '{data['drift_class']}'")
    if data["fixable"] not in _ALLOWED_FIXABLE:
        raise ValueError(f"{source}: fixable must be one of {sorted(_ALLOWED_FIXABLE)}")
    if data["visibility"] not in _ALLOWED_VISIBILITY:
        raise ValueError(f"{source}: visibility must be one of {sorted(_ALLOWED_VISIBILITY)}")
    if data["vendor"] not in _ALLOWED_VENDOR:
        raise ValueError(f"{source}: vendor must be one of {sorted(_ALLOWED_VENDOR)}")
    if not isinstance(data["seed"], int):
        raise ValueError(f"{source}: seed must be int")
    if not isinstance(data["description"], str) or not data["description"]:
        raise ValueError(f"{source}: description must be non-empty string")
    if not isinstance(data["expected"], dict):
        raise ValueError(f"{source}: expected must be a mapping")
    # id must match filename (without .yaml) and be valid scenario id
    stem = pathlib.Path(source).stem
    if data["id"] != stem:
        raise ValueError(f"{source}: id '{data['id']}' does not match filename '{stem}'")
    # Validate expected.fails is bool if present
    if "fails" in data["expected"] and not isinstance(data["expected"]["fails"], bool):
        raise ValueError(f"{source}: expected.fails must be bool")


def _load_yaml_file(path: pathlib.Path) -> Scenario:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    _validate_scenario_dict(data, str(path))
    return Scenario(
        id=data["id"],
        drift_class=data["drift_class"],
        fixable=data["fixable"],
        visibility=data["visibility"],
        vendor=data["vendor"],
        seed=int(data["seed"]),
        description=str(data["description"]),
        expected=dict(data["expected"]),
        raw=data,
    )


def load_scenario(scenario_id: str) -> Scenario:
    """Load a single scenario by id."""
    path = _SCENARIOS_DIR / f"{scenario_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"scenario not found: {scenario_id} (expected {path})")
    return _load_yaml_file(path)


def load_all_scenarios() -> list[Scenario]:
    """Load every ``*.yaml`` file in this directory, sorted by id."""
    scenarios: list[Scenario] = []
    for path in sorted(_SCENARIOS_DIR.glob("*.yaml")):
        scenarios.append(_load_yaml_file(path))
    return scenarios


def catalog_check_consistency() -> None:
    """Assert YAML catalog matches ``simulators.common.faults.SCENARIO_CATALOG``.

    Raises ``ValueError`` / ``AssertionError`` with a precise message when
    they drift apart. Called by the W2.5 unit test and by the verifier's
    readyz probe.
    """
    from simulators.common.faults import SCENARIO_CATALOG

    yaml_scenarios = load_all_scenarios()
    yaml_ids = {s.id for s in yaml_scenarios}
    catalog_ids = {e["id"] for e in SCENARIO_CATALOG}
    if yaml_ids != catalog_ids:
        missing_in_yaml = catalog_ids - yaml_ids
        missing_in_catalog = yaml_ids - catalog_ids
        raise AssertionError(
            f"Catalog mismatch: missing_in_yaml={missing_in_yaml} missing_in_catalog={missing_in_catalog}"
        )
    catalog_by_id = {e["id"]: e for e in SCENARIO_CATALOG}
    for scen in yaml_scenarios:
        entry = catalog_by_id[scen.id]
        if scen.drift_class != entry["drift_class"]:
            raise AssertionError(
                f"{scen.id}: drift_class yaml={scen.drift_class!r} catalog={entry['drift_class']!r}"
            )
        if scen.fixable != entry["fixable"]:
            raise AssertionError(f"{scen.id}: fixable yaml={scen.fixable!r} catalog={entry['fixable']!r}")
        if scen.visibility != entry["visibility"]:
            raise AssertionError(f"{scen.id}: visibility yaml={scen.visibility!r} catalog={entry['visibility']!r}")
