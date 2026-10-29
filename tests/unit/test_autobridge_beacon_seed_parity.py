"""Beacon seed-derivation parity (REQ-025): bench side == generator side.

The harness passes only seeds, so both sides derive them independently:
``beacon_client`` (bench, HTTP + BLS) and ``faultgen`` (simulators,
instance generation). If either formula drifts, the run and the services
derive different instances — this test pins them byte-identical.

Trust-boundary note: this test file (not shipped code) may import both
packages; the shipped contract (import-linter + trust-boundary AST test)
only constrains ``src/``, ``verifier/`` and ``simulators/`` trees.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import simulators.common.faultgen as faultgen
from bench import beacon_client


def test_eval_seed_parity_fixed_vectors() -> None:
    fixture_randomness = json.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "beacon" / "round_6593481.json").read_text(
            encoding="utf-8"
        )
    )["beacon"]["randomness"]
    for randomness_hex in ("00" * 32, "ff" * 32, fixture_randomness):
        assert beacon_client.derive_eval_seed(randomness_hex) == faultgen.derive_eval_seed(randomness_hex)


def test_instance_seed_parity_fixed_vectors() -> None:
    eval_seed = bytes.fromhex("05ec57a0c1e763e985c9ac18df2313a98392a9c24ecbceefd0b21216a9beaefa")
    for index in (0, 1, 7, 999, 10000):
        assert beacon_client.derive_instance_seed(eval_seed, index) == faultgen.derive_instance_seed(
            eval_seed, index
        )


def test_eval_domain_label_shared() -> None:
    """Both sides mix the same domain label (a silent fork breaks parity)."""
    assert beacon_client.EVAL_LABEL == faultgen.BEACON_LABEL == "autobridge-eval-v1"


def test_parity_rejects_malformed_symmetrically() -> None:
    for bad_randomness in ("", "zz", "ab" * 16):
        with pytest.raises(beacon_client.BeaconError):
            beacon_client.derive_eval_seed(bad_randomness)
        with pytest.raises(ValueError):
            faultgen.derive_eval_seed(bad_randomness)
