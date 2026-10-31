"""REQ-066: the beacon protocol pins the frontier model.

``freeze()`` writes ``beacon-frontier-model`` (from
``autobridge.config.frontier_model()``, the same resolution every arm
uses) plus ``beacon-agent-model`` when the opencode form differs;
``parse_protocol`` reads them; ``bench-beacon`` (real runs and
rehearsals) refuses to start on a mismatch. A protocol without the line
(older rehearsals) still parses: rehearsals run unpinned with a warning,
real runs refuse without it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from bench import beacon_run
from bench.beacon_run import BeaconError

if TYPE_CHECKING:
    import pathlib

FAKE_INFO = {
    "public_key": "868f005eb8e6e4ca0a47c8a77ceaa5309a47978a7c71bc5cce96366b5d7a569937c529eeda66c7293784a9402801af31",
    "period": 30,
    "genesis_time": 1595431050,
    "hash": beacon_run.beacon_client.LOE_MAINNET_CHAIN_HASH,
    "schemeID": beacon_run.beacon_client.LOE_SCHEME_ID,
}

OLD_PROTOCOL = """# Beacon evaluation protocol (REQ-025 item 4)

- beacon-round-after-utc: 2030-01-01T00:00:00+00:00
- beacon-target-round: 6599930
- beacon-faults: 12
- beacon-trials: 1
- beacon-arms: autobridge_cold
- beacon-engine-commit: abc123
- beacon-generator-commit: def456
- beacon-frozen-at-utc: 2030-01-01T00:00:00+00:00
- beacon-rehearsal: true
"""


def _fake_freeze_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beacon_run, "_git_dirty", lambda: False)
    monkeypatch.setattr(
        beacon_run.beacon_client, "fetch_chain_info", lambda _base_url: dict(FAKE_INFO)
    )
    monkeypatch.setattr(beacon_run, "_git_commit", lambda: "abc123")


def _protocol(**over: Any) -> beacon_run.BeaconProtocol:
    base = dict(
        round_after_utc="2030-01-01T00:00:00+00:00", target_round=9999999,
        faults=1, trials=1, arms=("autobridge_cold",),
        engine_commit="a", generator_commit="b", frozen_at_utc="2030-01-01T00:00:00+00:00",
    )
    base.update(over)
    return beacon_run.BeaconProtocol(**base)  # type: ignore[arg-type]


def test_freeze_pins_frontier_and_agent_models(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FRONTIER_MODEL", "muse-spark-1.3-contributor")
    monkeypatch.delenv("OPENCODE_MODEL", raising=False)
    _fake_freeze_env(monkeypatch)
    path = beacon_run.freeze("2030-01-01T00:00:00+00:00", 3, trials=1,
                             protocol_path=tmp_path / "protocol.md")
    text = path.read_text(encoding="utf-8")
    assert "- beacon-frontier-model: muse-spark-1.3-contributor" in text
    assert "- beacon-agent-model: meta/muse-spark-1.3-contributor" in text
    protocol = beacon_run.parse_protocol(path)
    assert protocol.frontier_model == "muse-spark-1.3-contributor"
    assert protocol.agent_model == "meta/muse-spark-1.3-contributor"


def test_freeze_omits_agent_line_when_same_form(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FRONTIER_MODEL", "custom/model")
    monkeypatch.setenv("OPENCODE_MODEL", "custom/model")
    _fake_freeze_env(monkeypatch)
    path = beacon_run.freeze("2030-01-01T00:00:00+00:00", 1, trials=1,
                             protocol_path=tmp_path / "protocol.md")
    assert "beacon-agent-model" not in path.read_text(encoding="utf-8")
    assert beacon_run.parse_protocol(path).agent_model is None


def test_old_protocol_without_line_parses(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "protocol.md"
    path.write_text(OLD_PROTOCOL, encoding="utf-8")
    protocol = beacon_run.parse_protocol(path)
    assert protocol.frontier_model is None
    assert protocol.agent_model is None
    assert protocol.rehearsal is True


def test_require_pinned_model_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRONTIER_MODEL", "muse-spark-1.3-contributor")
    monkeypatch.delenv("OPENCODE_MODEL", raising=False)
    beacon_run.require_pinned_model(_protocol(
        frontier_model="muse-spark-1.3-contributor",
        agent_model="meta/muse-spark-1.3-contributor",
    ))


def test_require_pinned_model_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRONTIER_MODEL", "model-b")
    monkeypatch.delenv("OPENCODE_MODEL", raising=False)
    with pytest.raises(BeaconError, match="model-b.*differs.*model-a"):
        beacon_run.require_pinned_model(_protocol(frontier_model="model-a"))


def test_require_pinned_model_agent_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRONTIER_MODEL", "muse-spark-1.3-contributor")
    monkeypatch.setenv("OPENCODE_MODEL", "other/agent")
    with pytest.raises(BeaconError, match="agent model.*differs"):
        beacon_run.require_pinned_model(_protocol(
            frontier_model="muse-spark-1.3-contributor",
            agent_model="meta/muse-spark-1.3-contributor",
        ))


def test_require_pinned_model_missing_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("FRONTIER_MODEL", raising=False)
    # Older rehearsal protocols run unpinned with a warning.
    beacon_run.require_pinned_model(_protocol(rehearsal=True))
    assert "unpinned" in capsys.readouterr().out
    # A real run requires the line.
    with pytest.raises(BeaconError, match="no beacon-frontier-model"):
        beacon_run.require_pinned_model(_protocol(rehearsal=False))


def test_run_beacon_refuses_mismatch_before_any_spend(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate runs first: no network mocks, still a clean refusal."""
    monkeypatch.setenv("FRONTIER_MODEL", "model-b")
    monkeypatch.delenv("OPENCODE_MODEL", raising=False)
    with pytest.raises(BeaconError, match="model-b.*differs.*model-a"):
        beacon_run.run_beacon(
            _protocol(frontier_model="model-a"),
            output_dir=tmp_path / "beacon" / "9999999",
        )


def test_run_beacon_requires_pin_for_real_run(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FRONTIER_MODEL", raising=False)
    with pytest.raises(BeaconError, match="no beacon-frontier-model"):
        beacon_run.run_beacon(
            _protocol(rehearsal=False),
            output_dir=tmp_path / "beacon" / "9999999",
        )
