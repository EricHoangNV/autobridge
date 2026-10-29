"""drand beacon client for the REQ-025 beacon evaluation (bench side).

Fetches League of Entropy mainnet rounds over HTTPS, verifies the
threshold BLS signature against the chain public key with the pinned
pure-Python ``py_ecc`` library, and derives the eval seed. Fails closed:
any fetch, format or signature problem raises, never a silent fallback.

What is verified (drand ``pedersen-bls-chained``, Go source
``crypto/schemes.go`` ``NewPedersenBLSChained``):

* the chain ``/info`` reports the pinned mainnet chain hash and scheme id
  (refuses any other chain — a test chain must never seed an eval);
* ``randomness == sha256(signature)`` (drand ``RandomnessFromSignature``);
* ``BLS.Verify(chain_pubkey, sha256(prev_sig || round_be64), signature)``
  with the standard RFC 9380 G2 ciphersuite (keys on G1, sigs on G2).

Trust boundary (DIRECTIVES §2): bench-side only. This module never
imports ``verifier`` or ``simulators``; it passes only the derived seed
to the simulator/verifier services, which generate instances internally.
The seed derivation mirrors ``simulators/common/faultgen.py``
(``derive_eval_seed`` / ``derive_instance_seed``); parity is pinned by
``tests/unit/test_beacon_seed_parity.py`` so the two sides can never drift.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

#: League of Entropy mainnet HTTP API (REQ-025 item 3).
DRAND_DEFAULT_BASE_URL = "https://api.drand.sh"

#: Pinned LoE mainnet chain hash (``/info`` ``hash``). Refuse anything else.
LOE_MAINNET_CHAIN_HASH = "8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce"

#: Pinned scheme id (``/info`` ``schemeID``).
LOE_SCHEME_ID = "pedersen-bls-chained"

#: Must equal ``simulators.common.faultgen.BEACON_LABEL`` (parity-tested).
EVAL_LABEL = "autobridge-eval-v1"

_FETCH_TIMEOUT_S = 20.0


class BeaconError(RuntimeError):
    """A beacon fetch or verification step failed (fail closed)."""


def _http_get_json(url: str, timeout_s: float = _FETCH_TIMEOUT_S) -> dict[str, Any]:
    """GET *url* and return the decoded JSON object (fail closed)."""
    import httpx as _httpx

    try:
        resp = _httpx.get(url, timeout=timeout_s, follow_redirects=True)
    except Exception as exc:
        raise BeaconError(f"beacon fetch failed for {url}: {type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise BeaconError(f"beacon fetch {url}: HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception as exc:
        raise BeaconError(f"beacon fetch {url}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BeaconError(f"beacon fetch {url}: expected a JSON object")
    return data


def fetch_chain_info(base_url: str = DRAND_DEFAULT_BASE_URL) -> dict[str, Any]:
    """Fetch and minimally validate the drand chain ``/info`` document."""
    info = _http_get_json(base_url.rstrip("/") + "/info")
    for field in ("public_key", "period", "genesis_time", "hash", "schemeID"):
        if field not in info:
            raise BeaconError(f"chain info missing field {field!r}")
    if str(info["schemeID"]) != LOE_SCHEME_ID:
        raise BeaconError(f"refusing non-mainnet scheme {info['schemeID']!r} (want {LOE_SCHEME_ID!r})")
    if str(info["hash"]) != LOE_MAINNET_CHAIN_HASH:
        raise BeaconError("refusing unknown drand chain (hash mismatch with pinned LoE mainnet)")
    try:
        period = int(info["period"])
        genesis = int(info["genesis_time"])
    except (TypeError, ValueError) as exc:
        raise BeaconError(f"chain info has non-integer period/genesis_time: {exc}") from exc
    if period <= 0 or genesis <= 0:
        raise BeaconError("chain info has non-positive period/genesis_time")
    _check_pubkey(str(info["public_key"]))
    return info


def _check_pubkey(public_key_hex: str) -> bytes:
    """Parse the 48-byte G1 chain public key (fail closed)."""
    try:
        raw = bytes.fromhex(public_key_hex.strip())
    except (ValueError, AttributeError) as exc:
        raise BeaconError(f"chain public key is not hex: {exc}") from exc
    if len(raw) != 48:
        raise BeaconError(f"chain public key must be 48 bytes, got {len(raw)}")
    return raw


def fetch_beacon(base_url: str, round: int) -> dict[str, Any]:
    """Fetch one public beacon round (``/public/<round>``)."""
    if not isinstance(round, int) or isinstance(round, bool) or round < 1:
        raise BeaconError(f"round must be a positive int, got {round!r}")
    data = _http_get_json(f"{base_url.rstrip('/')}/public/{round}")
    for field in ("round", "randomness", "signature", "previous_signature"):
        if field not in data:
            raise BeaconError(f"beacon round {round} missing field {field!r}")
    try:
        got_round = int(data["round"])
    except (TypeError, ValueError) as exc:
        raise BeaconError(f"beacon round field is not an int: {exc}") from exc
    if got_round != round:
        raise BeaconError(f"beacon round mismatch: asked {round}, got {got_round}")
    return data


def fetch_latest(base_url: str = DRAND_DEFAULT_BASE_URL) -> dict[str, Any]:
    """Fetch the latest published beacon round."""
    data = _http_get_json(base_url.rstrip("/") + "/public/latest")
    for field in ("round", "randomness", "signature", "previous_signature"):
        if field not in data:
            raise BeaconError(f"latest beacon missing field {field!r}")
    return data


def round_for_time(genesis_time: int, period: int, when_utc_epoch_s: int | float) -> int:
    """First round published at or after *when_utc_epoch_s* (fail closed).

    Round ``r`` is published at ``genesis_time + (r - 1) * period``.
    """
    if period <= 0 or genesis_time <= 0:
        raise BeaconError("round_for_time needs positive genesis_time and period")
    when = int(when_utc_epoch_s)
    if when < genesis_time:
        raise BeaconError("round_for_time: time predates chain genesis")
    # Ceiling division: round r publishes at genesis+(r-1)*period, so the
    # first round at/after `when` is ceil((when-genesis)/period)+1.
    return -(-(when - genesis_time) // period) + 1


def round_time_utc(genesis_time: int, period: int, round: int) -> int:
    """Publication epoch seconds of *round* (inverse of :func:`round_for_time`)."""
    if not isinstance(round, int) or isinstance(round, bool) or round < 1:
        raise BeaconError(f"round must be a positive int, got {round!r}")
    return int(genesis_time) + (round - 1) * int(period)


def verify_beacon(chain_info: dict[str, Any], beacon: dict[str, Any]) -> bytes:
    """Verify a beacon round against chain info; return the 32-byte randomness.

    Checks (all fail closed with :class:`BeaconError`):

    1. ``randomness == sha256(signature)`` (drand ``RandomnessFromSignature``);
    2. ``BLS.Verify(pubkey, sha256(prev_sig || round_be64), sig)`` with the
       pinned ``py_ecc`` G2 ciphersuite (``pedersen-bls-chained`` digest).

    Never returns False: a bad round raises.
    """
    try:
        public_key = _check_pubkey(str(chain_info["public_key"]))
        round = int(beacon["round"])
        randomness = bytes.fromhex(str(beacon["randomness"]).strip())
        signature = bytes.fromhex(str(beacon["signature"]).strip())
        previous = bytes.fromhex(str(beacon["previous_signature"]).strip())
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise BeaconError(f"beacon has malformed fields: {exc}") from exc
    if round < 1:
        raise BeaconError(f"beacon round must be >= 1, got {round}")
    if len(randomness) != 32:
        raise BeaconError(f"beacon randomness must be 32 bytes, got {len(randomness)}")
    if len(signature) != 96:
        raise BeaconError(f"beacon signature must be 96 bytes, got {len(signature)}")
    if len(previous) != 96:
        raise BeaconError(f"beacon previous_signature must be 96 bytes, got {len(previous)}")
    if hashlib.sha256(signature).digest() != randomness:
        raise BeaconError(f"beacon round {round}: randomness != sha256(signature)")
    from py_ecc.bls.ciphersuites import G2Basic as _G2Basic

    digest = hashlib.sha256(previous + round.to_bytes(8, "big")).digest()
    try:
        from eth_typing.bls import BLSPubkey as _BLSPubkey
        from eth_typing.bls import BLSSignature as _BLSSignature

        ok = _G2Basic.Verify(_BLSPubkey(public_key), digest, _BLSSignature(signature))
    except Exception as exc:
        raise BeaconError(f"beacon round {round}: BLS verify errored: {exc}") from exc
    if not ok:
        raise BeaconError(f"beacon round {round}: BLS signature invalid (chain key mismatch?)")
    return randomness


def derive_eval_seed(randomness_hex: str) -> bytes:
    """Derive the 32-byte eval seed from beacon randomness hex.

    ``eval_seed = sha256(randomness || "|" || EVAL_LABEL)`` — mirrors
    ``faultgen.derive_eval_seed`` (parity-tested, never hand-edit one side).
    """
    try:
        raw = bytes.fromhex(randomness_hex.strip())
    except (ValueError, AttributeError) as exc:
        raise BeaconError(f"randomness must be hex: {exc}") from exc
    if len(raw) != 32:
        raise BeaconError(f"randomness must be 32 bytes, got {len(raw)}")
    return hashlib.sha256(raw + b"|" + EVAL_LABEL.encode("ascii")).digest()


def derive_instance_seed(eval_seed: bytes, index: int) -> bytes:
    """Derive the per-instance seed: ``sha256(eval_seed || index_be8)``."""
    if not isinstance(eval_seed, bytes) or len(eval_seed) != 32:
        raise BeaconError("eval_seed must be 32 bytes")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise BeaconError("index must be a non-negative int")
    return hashlib.sha256(eval_seed + index.to_bytes(8, "big")).digest()


@dataclass(frozen=True)
class VerifiedRound:
    """A fetched-and-verified beacon round with derived seeds."""

    round: int
    randomness_hex: str
    eval_seed_hex: str
    published_at_utc: int


def fetch_verified_round(
    base_url: str,
    round: int,
    *,
    chain_info: dict[str, Any] | None = None,
) -> VerifiedRound:
    """Fetch chain info + round, verify, derive seeds (fail closed)."""
    info = chain_info if chain_info is not None else fetch_chain_info(base_url)
    beacon = fetch_beacon(base_url, round)
    randomness = verify_beacon(info, beacon)
    eval_seed = hashlib.sha256(randomness + b"|" + EVAL_LABEL.encode("ascii")).digest()
    published_at = round_time_utc(int(info["genesis_time"]), int(info["period"]), round)
    return VerifiedRound(
        round=round,
        randomness_hex=randomness.hex(),
        eval_seed_hex=eval_seed.hex(),
        published_at_utc=published_at,
    )


def evidence_line(info: dict[str, Any], verified: VerifiedRound) -> str:
    """One-line public evidence for the protocol/run record (no secrets)."""
    import datetime as _dt

    when = _dt.datetime.fromtimestamp(verified.published_at_utc, tz=_dt.UTC).isoformat()
    chain_hash = str(info.get("hash", LOE_MAINNET_CHAIN_HASH))
    return (
        f"round={verified.round} published_at={when} "
        f"randomness={verified.randomness_hex} "
        f"eval_seed={verified.eval_seed_hex} "
        f"chain={chain_hash[:12]} scheme={LOE_SCHEME_ID} "
        f"verify_url={DRAND_DEFAULT_BASE_URL}/public/{verified.round}"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: ``verify --round N`` or ``round-for --utc ISO`` (fail-loud)."""
    import argparse
    import datetime as _dt

    parser = argparse.ArgumentParser(description="drand beacon client (REQ-025): fetch + verify + derive")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=DRAND_DEFAULT_BASE_URL)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_verify = sub.add_parser("verify", parents=[common], help="fetch, verify and derive seeds for a round")
    p_verify.add_argument("--round", type=int, required=True)
    p_for = sub.add_parser("round-for", parents=[common], help="first round published at/after a UTC time")
    p_for.add_argument("--utc", required=True, help="ISO UTC time, e.g. 2026-11-05T12:00:00+00:00")
    args = parser.parse_args(argv)
    try:
        info = fetch_chain_info(args.base_url)
        if args.cmd == "verify":
            verified = fetch_verified_round(args.base_url, args.round, chain_info=info)
            print(evidence_line(info, verified), flush=True)
            print(json.dumps({"round": verified.round, "eval_seed": verified.eval_seed_hex}), flush=True)
        else:
            when = _dt.datetime.fromisoformat(args.utc)
            if when.tzinfo is None:
                when = when.replace(tzinfo=_dt.UTC)
            epoch = int(when.timestamp())
            r = round_for_time(int(info["genesis_time"]), int(info["period"]), epoch)
            pub = round_time_utc(int(info["genesis_time"]), int(info["period"]), r)
            print(f"round={r} published_at={pub}", flush=True)
    except BeaconError as exc:
        print(f"beacon client failed: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
