"""Beacon client tests (REQ-025 item 3): fetch, verify, derive — fail closed.

Network is never touched: the drand round is a committed public fixture
(`tests/fixtures/beacon/round_6593481.json` — beacon outputs are public
by design), and fetch paths are exercised through monkeypatched
transports. The one live property — that the pinned math verifies a real
mainnet round — is pinned by the fixture itself.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

import pytest

from bench import beacon_client
from bench.beacon_client import BeaconError

FIXTURE = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "beacon" / "round_6593481.json").read_text(
        encoding="utf-8"
    )
)


FIXTURE_EVAL_SEED = "05ec57a0c1e763e985c9ac18df2313a98392a9c24ecbceefd0b21216a9beaefa"


def test_fixture_verifies_offline() -> None:
    """The pinned BLS math verifies a real mainnet round (recorded)."""
    randomness = beacon_client.verify_beacon(FIXTURE["chain_info"], FIXTURE["beacon"])
    assert randomness.hex() == FIXTURE["beacon"]["randomness"]


def test_verify_rejects_tampered_randomness() -> None:
    bad = dict(FIXTURE["beacon"], randomness="00" * 32)
    with pytest.raises(BeaconError):
        beacon_client.verify_beacon(FIXTURE["chain_info"], bad)


def test_verify_rejects_tampered_signature() -> None:
    sig = bytearray.fromhex(FIXTURE["beacon"]["signature"])
    sig[10] ^= 0xFF
    bad = dict(FIXTURE["beacon"], signature=sig.hex())
    with pytest.raises(BeaconError):
        beacon_client.verify_beacon(FIXTURE["chain_info"], bad)


def test_verify_rejects_short_signature() -> None:
    bad = dict(FIXTURE["beacon"], signature="ab" * 48)
    with pytest.raises(BeaconError):
        beacon_client.verify_beacon(FIXTURE["chain_info"], bad)


def test_verify_rejects_bad_lengths() -> None:
    for field, value in (
        ("randomness", "ab" * 16),
        ("previous_signature", "ab" * 48),
        ("round", 0),
    ):
        bad = dict(FIXTURE["beacon"], **{field: value})
        with pytest.raises(BeaconError):
            beacon_client.verify_beacon(FIXTURE["chain_info"], bad)


def test_verify_rejects_missing_fields() -> None:
    bad = {k: v for k, v in FIXTURE["beacon"].items() if k != "signature"}
    with pytest.raises(BeaconError):
        beacon_client.verify_beacon(FIXTURE["chain_info"], bad)  # type: ignore[arg-type]


def test_derive_eval_seed_known_vector() -> None:
    randomness_hex = FIXTURE["beacon"]["randomness"]
    raw = bytes.fromhex(randomness_hex)
    want = hashlib.sha256(raw + b"|autobridge-eval-v1").digest()
    assert beacon_client.derive_eval_seed(randomness_hex) == want
    assert beacon_client.derive_eval_seed(randomness_hex).hex() == FIXTURE_EVAL_SEED


def test_derive_eval_seed_rejects_garbage() -> None:
    for bad in ("", "zz", "ab" * 16, "ab" * 64):
        with pytest.raises(BeaconError):
            beacon_client.derive_eval_seed(bad)


def test_derive_instance_seed_known_vector() -> None:
    eval_seed = bytes.fromhex(FIXTURE_EVAL_SEED)
    want = hashlib.sha256(eval_seed + (7).to_bytes(8, "big")).digest()
    assert beacon_client.derive_instance_seed(eval_seed, 7) == want


def test_derive_instance_seed_rejects_garbage() -> None:
    with pytest.raises(BeaconError):
        beacon_client.derive_instance_seed(b"short", 0)
    for bad in (-1, True, "0"):
        with pytest.raises(BeaconError):
            beacon_client.derive_instance_seed(bytes(32), bad)  # type: ignore[arg-type]


def test_round_for_time_math() -> None:
    genesis, period = 1595431050, 30
    # Round r publishes at genesis + (r-1)*period; first round at/after T.
    assert beacon_client.round_for_time(genesis, period, genesis) == 1
    assert beacon_client.round_for_time(genesis, period, genesis + 30) == 2
    assert beacon_client.round_for_time(genesis, period, genesis + 29) == 2
    assert beacon_client.round_time_utc(genesis, period, 1) == genesis
    assert beacon_client.round_time_utc(genesis, period, 6593481) == 1595431050 + 6593480 * 30
    with pytest.raises(BeaconError):
        beacon_client.round_for_time(genesis, period, genesis - 1)
    with pytest.raises(BeaconError):
        beacon_client.round_for_time(genesis, 0, genesis)


def test_fetch_chain_info_rejects_foreign_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-mainnet /info (wrong hash/scheme) is refused — fail closed."""
    import copy

    foreign = copy.deepcopy(FIXTURE["chain_info"])
    foreign["hash"] = "00" * 32
    monkeypatch.setattr(beacon_client, "_http_get_json", lambda _url, _timeout_s=20.0: dict(foreign))
    with pytest.raises(BeaconError):
        beacon_client.fetch_chain_info("https://example.invalid")
    foreign["hash"] = beacon_client.LOE_MAINNET_CHAIN_HASH
    foreign["schemeID"] = "pedersen-bls-unchained"
    monkeypatch.setattr(beacon_client, "_http_get_json", lambda _url, _timeout_s=20.0: dict(foreign))
    with pytest.raises(BeaconError):
        beacon_client.fetch_chain_info("https://example.invalid")


def test_fetch_beacon_rejects_round_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        beacon_client, "_http_get_json", lambda _url, _timeout_s=20.0: dict(FIXTURE["beacon"])
    )
    with pytest.raises(BeaconError):
        beacon_client.fetch_beacon("https://example.invalid", 123)
    assert beacon_client.fetch_beacon("https://example.invalid", 6593481)["round"] == 6593481
    with pytest.raises(BeaconError):
        beacon_client.fetch_beacon("https://example.invalid", 0)


def test_fetch_verified_round_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch → verify → derive through the fixture (no network)."""

    def fake_get(url: str, _timeout_s: float = 20.0) -> dict[str, Any]:
        if url.endswith("/info"):
            return dict(FIXTURE["chain_info"])
        assert url.endswith("/public/6593481")
        return dict(FIXTURE["beacon"])

    monkeypatch.setattr(beacon_client, "_http_get_json", fake_get)
    verified = beacon_client.fetch_verified_round("https://example.invalid", 6593481)
    assert verified.round == 6593481
    assert verified.eval_seed_hex == FIXTURE_EVAL_SEED
    line = beacon_client.evidence_line(FIXTURE["chain_info"], verified)
    assert "round=6593481" in line and FIXTURE_EVAL_SEED in line


def test_cli_verify_and_round_for(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        beacon_client, "_http_get_json",
        lambda url, _timeout_s=20.0: dict(FIXTURE["chain_info"]) if url.endswith("/info") else dict(FIXTURE["beacon"])  # noqa: ARG005,
    )
    assert beacon_client.main(["verify", "--round", "6593481", "--base-url", "https://example.invalid"]) == 0
    out = capsys.readouterr().out
    assert "round=6593481" in out
    assert beacon_client.main(["round-for", "--utc", "2026-10-29T00:57:30+00:00"]) == 0
    out = capsys.readouterr().out
    assert "round=6593481" in out
    assert beacon_client.main(["verify", "--round", "0", "--base-url", "https://example.invalid"]) == 1
