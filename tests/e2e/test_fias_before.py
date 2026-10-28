"""REQ-022 sub-step C "before": the vocab-v1/v2 engine refuses a FIAS source.

With the PABX simulator and document viewer in place and the engine still
on the HTTP-only vocabulary, a build from the FIAS interface document must
stop cleanly: no success, no draft spec (so no release is persisted), a
named reason (``fias_tcp`` + the pinned vocabulary), escalation to L4, and
zero model calls — never a half-working connector with invented HTTP
operations.

The in-process test pins this for the suite; the live test additionally
checks the real PABX surface answers while the docs still refuse.
"""

from __future__ import annotations

import pathlib
import urllib.request

from autobridge.discovery.bootstrap import build_draft_spec

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FIAS_MD_PATH = REPO_ROOT / "simulators" / "docs" / "fias-pabx.md"
FIAS_MD = FIAS_MD_PATH.read_text(encoding="utf-8")


def _fias_build(vocabulary: str):  # type: ignore[no-untyped-def]
    return build_draft_spec(
        FIAS_MD,
        source="simulators/docs/fias-pabx.md",
        connector_id="fiaspabx",
        spec_vocabulary=vocabulary,
    )


def test_fias_before_refuses_in_process() -> None:
    """Spec-v1 and spec-v2 engines refuse FIAS docs with a named reason, 0 calls."""
    for vocabulary in ("v1", "v2"):
        result = _fias_build(vocabulary)
        assert not result.success
        assert result.draft_spec is None
        assert result.degradation_reason is not None
        assert "fias_tcp" in result.degradation_reason
        assert f"ConnectorSpec {vocabulary}" in result.degradation_reason
        assert result.model_usage.get("calls", 0) == 0
        assert result.model_usage.get("frontier_tokens", 0) == 0
        assert result.model_usage.get("small_tokens", 0) == 0
        assert any(t.get("stage") == "vocabulary_guard" for t in result.trace)


def test_fias_before_live_surface_when_stack_up(e2e_stack: str) -> None:
    """Live PABX answers health/readydocs; the same docs still refuse (v1, v2)."""
    assert e2e_stack
    from tests.e2e.conftest import service_url

    with urllib.request.urlopen(service_url("sim-fias-pabx", "/healthz"), timeout=10) as resp:
        assert resp.status == 200
    with urllib.request.urlopen(service_url("sim-fias-pabx", "/developers/source"), timeout=10) as resp:
        assert resp.status == 200
        assert "FIAS" in resp.read().decode()
    for vocabulary in ("v1", "v2"):
        result = _fias_build(vocabulary)
        assert not result.success
        assert result.draft_spec is None
