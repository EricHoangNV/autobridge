"""REQ-014 — host tools read `.env`; empty values never override defaults.

Covers the `.env` loader for host targets (``scripts/load_dotenv.sh``),
its wiring into the Makefile host targets (smoke/e2e/demo/bench), and the
empty-as-unset handling in the code that reads ``E2E_TIMEOUT``,
``DATABASE_URL`` and the demo/edge env knobs. No Docker, no network: the
loader is exercised in a subprocess shell, everything else in-process.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from autobridge.bench import demo as demo_mod
from autobridge.control import models as models_mod

ROOT = Path(__file__).resolve().parents[2]
LOADER = ROOT / "scripts" / "load_dotenv.sh"
MAKEFILE = ROOT / "Makefile"

THREE_TOKENS = """\
SIM_ADMIN_TOKEN=tok-smoke-abc123
AUTOBRIDGE_CONTROL_TOKEN=tok-ctrl-def456
EDGE_RELEASE_SECRET=tok-edge-ghi789
"""


def _source(dotenv: Path, extra_env: dict[str, str] | None = None, pre: str = "") -> dict[str, str]:
    """Source the loader against *dotenv* and report selected variables."""
    probe = "SIM_ADMIN_TOKEN AUTOBRIDGE_CONTROL_TOKEN EDGE_RELEASE_SECRET E2E_TIMEOUT EMPTY_VAL QUOTED_HASH EXPORTED_KEY"
    script = f"{pre}. ./scripts/load_dotenv.sh; " + "; ".join(
        'printf "' + v + '=%s\\n" "${' + v + ":-(unset)}\""
        for v in probe.split()
    )
    env = dict(os.environ)
    env["DOTENV_PATH"] = str(dotenv)
    # Isolate from the developer's own .env-driven shell for determinism.
    for var in probe.split():
        env.pop(var, None)
    if extra_env:
        env.update(extra_env)
    out = subprocess.run(
        ["bash", "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, f"loader failed: {out.stderr}"
    result = {}
    for line in out.stdout.splitlines():
        key, _, val = line.partition("=")
        result[key] = val
    return result


def test_loader_exports_nonempty_values_and_ignores_comments(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# full-line comment\n"
        "\n"
        "SIM_ADMIN_TOKEN=tok-smoke-abc123 # inline note\n"
        "AUTOBRIDGE_CONTROL_TOKEN=\"ctrl tok\"\n"
        "EDGE_RELEASE_SECRET='sec#notcomment'\n"
        "QUOTED_HASH=\"a # b\" # trailing comment\n"
        "EMPTY_VAL=\n"
        "E2E_TIMEOUT=   \n"
        "export EXPORTED_KEY=expval\n"
        "1BAD=skip\n"
        "NOEQUALS\n"
    )
    got = _source(dotenv)
    assert got["SIM_ADMIN_TOKEN"] == "tok-smoke-abc123"
    assert got["AUTOBRIDGE_CONTROL_TOKEN"] == "ctrl tok"
    assert got["EDGE_RELEASE_SECRET"] == "sec#notcomment"
    assert got["QUOTED_HASH"] == "a # b"
    assert got["EXPORTED_KEY"] == "expval"
    assert got["EMPTY_VAL"] == "(unset)"
    # E2E_TIMEOUT= (blank) must not land in the environment at all, so
    # `float('')` in tests/e2e/conftest.py can never see it.
    assert got["E2E_TIMEOUT"] == "(unset)"


def test_loader_explicit_environment_wins(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("SIM_ADMIN_TOKEN=from-file\n")
    got = _source(dotenv, extra_env={"SIM_ADMIN_TOKEN": "from-shell"})
    assert got["SIM_ADMIN_TOKEN"] == "from-shell"


def test_loader_noop_without_dotenv(tmp_path: Path) -> None:
    got = _source(tmp_path / ".env")
    assert got["SIM_ADMIN_TOKEN"] == "(unset)"


def test_loader_three_tokens_enable_smoke_token_branch(tmp_path: Path) -> None:
    """The REQ-014 acceptance shape: with the three tokens in a temp `.env`
    and no manual export, the `SIM_ADMIN_TOKEN` gate in `scripts/smoke.sh`
    (`[ -n "${SIM_ADMIN_TOKEN:-}" ]`) takes the enforcing branch."""
    dotenv = tmp_path / ".env"
    dotenv.write_text(THREE_TOKENS)
    script = '. ./scripts/load_dotenv.sh; [ -n "${SIM_ADMIN_TOKEN:-}" ] && [ -n "${AUTOBRIDGE_CONTROL_TOKEN:-}" ] && [ -n "${EDGE_RELEASE_SECRET:-}" ] && echo ENFORCED'
    env = dict(os.environ, DOTENV_PATH=str(dotenv))
    for var in ("SIM_ADMIN_TOKEN", "AUTOBRIDGE_CONTROL_TOKEN", "EDGE_RELEASE_SECRET"):
        env.pop(var, None)
    out = subprocess.run(["bash", "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0
    assert out.stdout.strip() == "ENFORCED"


def test_makefile_host_targets_load_dotenv() -> None:
    text = MAKEFILE.read_text()
    for target in ("smoke:", "e2e:", "bench:", "bench-heldout:", "demo:"):
        start = text.index(target)
        # Recipe block runs to the next top-level target or ` typical section`.
        end = len(text)
        for marker in ("\n# ----",):
            nxt = text.find(marker, start + len(target))
            if nxt != -1:
                end = min(end, nxt)
        block = text[start:end]
        assert "load_dotenv.sh" in block, f"make {target.rstrip(':')} must source scripts/load_dotenv.sh (REQ-014)"


def test_makefile_bench_heldout_reads_heldout_dir_from_shell() -> None:
    """`bench-heldout` must see HELDOUT_DIR from `.env`: the guard has to run
    in the shell after the loader, not as a make-level `$(HELDOUT_DIR)`."""
    text = MAKEFILE.read_text()
    start = text.index("bench-heldout:")
    block = text[start : text.find("\n# ----", start)]
    assert "$(HELDOUT_DIR)" not in block
    assert "${HELDOUT_DIR:-}" in block


def test_default_database_url_treats_blank_as_unset(monkeypatch: Any) -> None:
    monkeypatch.delenv(models_mod.DATABASE_URL_ENV, raising=False)
    assert models_mod.default_database_url() == models_mod.DEFAULT_SQLITE_URL
    monkeypatch.setenv(models_mod.DATABASE_URL_ENV, "")
    assert models_mod.default_database_url() == models_mod.DEFAULT_SQLITE_URL
    monkeypatch.setenv(models_mod.DATABASE_URL_ENV, "   ")
    assert models_mod.default_database_url() == models_mod.DEFAULT_SQLITE_URL
    monkeypatch.setenv(models_mod.DATABASE_URL_ENV, "sqlite:////tmp/x.db")
    assert models_mod.default_database_url() == "sqlite:////tmp/x.db"


def test_demo_env_default_treats_blank_as_unset(monkeypatch: Any) -> None:
    assert demo_mod.env_default("CONTROL_URL", demo_mod.DEFAULT_CONTROL_URL) == demo_mod.DEFAULT_CONTROL_URL
    monkeypatch.setenv("CONTROL_URL", "")
    assert demo_mod.env_default("CONTROL_URL", demo_mod.DEFAULT_CONTROL_URL) == demo_mod.DEFAULT_CONTROL_URL
    monkeypatch.setenv("CONTROL_URL", "   ")
    assert demo_mod.env_default("CONTROL_URL", demo_mod.DEFAULT_CONTROL_URL) == demo_mod.DEFAULT_CONTROL_URL
    monkeypatch.setenv("CONTROL_URL", "http://example:8101")
    assert demo_mod.env_default("CONTROL_URL", demo_mod.DEFAULT_CONTROL_URL) == "http://example:8101"
    monkeypatch.setenv("DEMO_SEED", "")
    assert int(demo_mod.env_default("DEMO_SEED", "0")) == 0


def test_e2e_timeout_treats_blank_as_unset(monkeypatch: Any) -> None:
    from tests.e2e import conftest as e2e_conftest

    monkeypatch.delenv("E2E_TIMEOUT", raising=False)
    assert e2e_conftest.e2e_timeout_seconds() == 120.0
    monkeypatch.setenv("E2E_TIMEOUT", "")
    assert e2e_conftest.e2e_timeout_seconds() == 120.0
    monkeypatch.setenv("E2E_TIMEOUT", "   ")
    assert e2e_conftest.e2e_timeout_seconds() == 120.0
    monkeypatch.setenv("E2E_TIMEOUT", "30")
    assert e2e_conftest.e2e_timeout_seconds() == 30.0


def test_edge_env_treats_blank_as_unset(monkeypatch: Any) -> None:
    from autobridge.edge import app as edge_app

    monkeypatch.delenv("EDGE_STORE_PATH", raising=False)
    monkeypatch.delenv("FIAS_LISTEN_HOST", raising=False)
    monkeypatch.delenv("FIAS_LISTEN_PORT", raising=False)
    assert edge_app._store_path() == "./edge_agent.db"
    assert edge_app._fias_host() == "0.0.0.0"
    assert edge_app._fias_port() == 5010
    monkeypatch.setenv("EDGE_STORE_PATH", "")
    monkeypatch.setenv("FIAS_LISTEN_HOST", "")
    monkeypatch.setenv("FIAS_LISTEN_PORT", "")
    assert edge_app._store_path() == "./edge_agent.db"
    assert edge_app._fias_host() == "0.0.0.0"
    assert edge_app._fias_port() == 5010
    monkeypatch.setenv("FIAS_LISTEN_PORT", "not-a-port")
    assert edge_app._fias_port() == 5010
