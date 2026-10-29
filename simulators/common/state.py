"""Shared mutable simulation state (fault switchboard).

The fault switchboard is a global singleton that holds the active scenario
and seed. Both the public API (port 8000, vendor-net) and the admin API
(port 8001, admin-net) import this module and therefore share state
within a single container process. When the simulator runs as two separate
processes (public + admin), they still share state via this module if
started with shared memory — but in our Docker setup the two listeners
are separate processes, so we use a file-backed state for cross-process
visibility. For the first cut (single-process + thread-safe fallback) we
use an in-memory dict with a lock and a JSON file on disk as the
synchronization point.

Simplification for W2.2: both listeners run in the same Python process
via a dual-uvicorn launcher, so in-memory is sufficient. The file path is
kept for future multi-process use.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

# Default scenario — no fault.
DEFAULT_SCENARIO = "baseline"
DEFAULT_SEED = 0

# File for cross-process state (used by dual-process launcher).
_STATE_FILE = Path(os.getenv("SIM_STATE_FILE", "/tmp/sim_state.json"))

_lock = threading.Lock()
_state: dict[str, Any] = {
    "scenario_id": DEFAULT_SCENARIO,
    "seed": DEFAULT_SEED,
    "request_count": 0,
    # REQ-025 beacon slot: {"eval_seed_hex": str, "index": int} or None.
    # When set, record-shape/behaviour serving follows the beacon instance
    # (simulators/common/beacon_serve.py); scenario predicates still apply
    # for anything the beacon family does not cover.
    "beacon": None,
}


def _load_file() -> dict[str, Any] | None:
    try:
        if _STATE_FILE.is_file():
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _save_file(data: dict[str, Any]) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def get_state() -> dict[str, Any]:
    """Return a copy of current state (scenario_id, seed, request_count)."""
    with _lock:
        # Prefer file if it exists and is newer (cross-process sync).
        file_state = _load_file()
        if file_state is not None:
            # Sync in-memory to file.
            _state.update(file_state)
        return dict(_state)


def set_scenario(scenario_id: str, seed: int) -> dict[str, Any]:
    """Activate *scenario_id* with *seed*, resetting request_count to 0."""
    with _lock:
        _state["scenario_id"] = scenario_id
        _state["seed"] = seed
        _state["request_count"] = 0
        # Scenario and beacon are exclusive serving modes.
        _state["beacon"] = None
        _save_file(dict(_state))
        return dict(_state)


def reset_state() -> dict[str, Any]:
    """Reset to baseline / seed 0."""
    return set_scenario(DEFAULT_SCENARIO, DEFAULT_SEED)


def set_beacon_state(eval_seed_hex: str | None, index: int | None) -> dict[str, Any]:
    """Set (or clear, with Nones) the active REQ-025 beacon slot."""
    with _lock:
        if eval_seed_hex is None or index is None:
            _state["beacon"] = None
        else:
            _state["beacon"] = {"eval_seed_hex": eval_seed_hex, "index": index}
        _state["request_count"] = 0
        _save_file(dict(_state))
        return dict(_state)


def increment_request_count() -> int:
    """Atomically increment and return new request_count."""
    with _lock:
        # Sync from file first.
        file_state = _load_file()
        if file_state is not None:
            _state.update(file_state)
        _state["request_count"] = int(_state.get("request_count", 0)) + 1
        _save_file(dict(_state))
        return int(_state["request_count"])


def get_request_count() -> int:
    with _lock:
        file_state = _load_file()
        if file_state is not None:
            _state.update(file_state)
        return int(_state.get("request_count", 0))
